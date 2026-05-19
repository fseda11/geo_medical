"""
cnes.py — Integração com a API pública do DATASUS/CNES
  - Busca estabelecimentos por município (com paginação)
  - Enriquece com dados de leitos, tipo, gestão
  - Calcula score de potencial de medicamentos de alto custo
  - Cache por município para evitar requisições repetidas
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import pandas as pd
import requests
import streamlit as st

from config import (
    CNES_BASE_URL,
    CNES_PAGE_SIZE,
    UNIT_TYPES,
    CATEGORY_MAP,
    SCORE_WEIGHTS,
    HIGH_COST_RELEVANT_TYPES,
    GOOGLE_API_KEY,
)
from municipalities import municipality_ibge_to_cnes_code


# ── Dicionários de decodificação CNES ─────────────────────────────────────────

GESTAO_LABELS = {
    "M": "Municipal",
    "E": "Estadual",
    "S": "Sem gestão (Privado)",
    "D": "Dupla (Municipal + Estadual)",
}

NATUREZA_LABELS = {
    "1000": "Órgão Público Federal",       "1014": "Autarquia Federal",
    "1023": "Empresa Pública Federal",     "1031": "Fundação Pública Federal",
    "1040": "Órgão Público Estadual",      "1104": "Autarquia Estadual",
    "1112": "Empresa Pública Estadual",    "1120": "Fundação Pública Estadual",
    "1139": "Órgão Público Municipal",     "1147": "Autarquia Municipal",
    "1155": "Empresa Pública Municipal",   "1163": "Fundação Pública Municipal",
    "2011": "Econ. Mista Federal",         "2038": "Econ. Mista Estadual",
    "2054": "Econ. Mista Municipal",
    "3034": "Serv. Social Autônomo",       "3069": "Fundação Privada",
    "3077": "Organização Religiosa",       "3085": "Entidade Sindical",
    "1244": "Serv. Social Autônomo (SESI/SESC)",
    "3131": "Cooperativa",                 "3999": "Associação Privada (ONG/OS)",
    "4000": "Empresa Privada",             "4030": "Soc. Ltda.",
    "4041": "Soc. Anônima (S/A)",          "4120": "Empresa Individual",
    "5010": "Empresário Individual (PF)",  "5069": "MEI",
}

def _dec_gestao(v: str) -> str:
    return GESTAO_LABELS.get(str(v).strip().upper(), v or "—")

def _dec_nat(v) -> str:
    s = str(v).strip() if v else ""
    if s in NATUREZA_LABELS:
        return NATUREZA_LABELS[s]
    try:
        n = int(s)
        if 1000 <= n < 2000: return "Entidade Pública"
        if 2000 <= n < 3000: return "Economia Mista"
        if 3000 <= n < 4000: return "Privado Sem Fins Lucrativos"
        if 4000 <= n < 5000: return "Empresa Privada"
        if n >= 5000:        return "Pessoa Física / MEI"
    except Exception:
        pass
    return s or "—"

def _yn(v) -> str:
    try:
        return "Sim" if int(v) else "Não"
    except Exception:
        return "—"

def _yn_str(v: str) -> str:
    s = str(v or "").strip().upper()
    if s == "SIM": return "Sim"
    if s == "NAO" or s == "NÃO": return "Não"
    return s or "—"

# ── Cache de telefones Google ──────────────────────────────────────────────────
_google_phone_cache: Dict[str, str] = {}

def _get_google_phone(name: str, city: str, category: str = "") -> str:
    """
    Busca telefone via Google Places em 2 passos:
    1. textsearch → place_id (melhor busca por nome comercial)
    2. place/details → formatted_phone_number
    """
    key = f"{name}|{city}"
    if key in _google_phone_cache:
        return _google_phone_cache[key]
    phone = ""
    try:
        # Passo 1: Text Search para achar o lugar por nome + cidade
        r1 = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={
                "query": f"{name} {city}",
                "region": "br",
                "key": GOOGLE_API_KEY,
            },
            timeout=4,
        )
        results = r1.json().get("results", [])
        place_id = results[0].get("place_id", "") if results else ""

        if place_id:
            # Passo 2: Place Details para pegar o telefone
            r2 = requests.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={
                    "place_id": place_id,
                    "fields":   "formatted_phone_number",
                    "key":      GOOGLE_API_KEY,
                },
                timeout=4,
            )
            phone = r2.json().get("result", {}).get("formatted_phone_number", "") or ""
    except Exception:
        phone = ""
    _google_phone_cache[key] = phone
    return phone


# ── Busca paginada por município ──────────────────────────────────────────────

def _fetch_cnes_municipality(co_municipio: str) -> List[Dict]:
    """
    Baixa todos os estabelecimentos de um município via CNES API.
    Cache de 1 hora por município.
    """
    url = f"{CNES_BASE_URL}/estabelecimentos"
    all_items: List[Dict] = []
    offset = 0
    max_pages = 30  # segurança contra loop infinito

    for _ in range(max_pages):
        params = {
            "codigo_municipio": co_municipio,
            "limit": CNES_PAGE_SIZE,
            "offset": offset,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("estabelecimentos", [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < CNES_PAGE_SIZE:
                break
            offset += CNES_PAGE_SIZE
            time.sleep(0.05)
        except Exception:
            break

    return all_items


# ── Score de potencial ────────────────────────────────────────────────────────

def _calc_score(row: pd.Series) -> int:
    """
    Score de potencial de consumo de medicamentos de alto custo.
    Máximo: 100 pontos.
    """
    score = 0

    # 1. Tipo de unidade (0–50)
    cat = row.get("category", "outro")
    score += SCORE_WEIGHTS.get(cat, 5)

    # 2. Capacidade via flags booleanas (0–30)
    leitos_proxy = int(row.get("qt_leito_internacao") or 0)
    if leitos_proxy >= 75:    score += 30
    elif leitos_proxy >= 50:  score += 20
    elif leitos_proxy >= 25:  score += 10
    elif leitos_proxy > 0:    score += 5

    # 3. Serviços adicionais (0–10) — suporta "Sim"/"Não" ou 0/1
    def _sb(v):
        if isinstance(v, str): return 1 if v.strip().lower() == "sim" else 0
        try: return int(v or 0)
        except: return 0
    score += min(10, (_sb(row.get("tem_cirurgia")) +
                      _sb(row.get("tem_obstetrico")) +
                      _sb(row.get("atend_ambulatorial"))) * 3)

    # 4. Gestão — aceita código ("M") ou decodificado ("Municipal")
    gestao = str(row.get("tp_gestao", "")).upper()
    if any(x in gestao for x in ("ESTADUAL", "FEDERAL", "SEM GEST")):
        score += 10
    elif "DUPLA" in gestao or gestao == "D":
        score += 6
    elif "MUNICIPAL" in gestao or gestao == "M":
        score += 4

    return min(score, 100)


# ── Enriquecimento e normalização ─────────────────────────────────────────────

def _normalize_establishment(est: Dict, muni_row: pd.Series) -> Dict:
    """
    Normaliza campos da API CNES — formato atual (nomes por extenso).
    Parâmetro correto: codigo_municipio (não co_municipio).
    """
    tp = est.get("codigo_tipo_unidade") or est.get("tp_unidade")
    try:
        tp = int(tp)
    except Exception:
        tp = 0

    category  = CATEGORY_MAP.get(tp, "outro")
    type_desc = UNIT_TYPES.get(tp, "Outro")

    lat = est.get("latitude_estabelecimento_decimo_grau") or muni_row.get("latitude")
    lng = est.get("longitude_estabelecimento_decimo_grau") or muni_row.get("longitude")
    try:
        lat = float(lat) if lat is not None else None
        lng = float(lng) if lng is not None else None
    except Exception:
        lat = muni_row.get("latitude")
        lng = muni_row.get("longitude")

    tem_internacao = int(est.get("estabelecimento_possui_atendimento_hospitalar") or 0)
    tem_cirurgia   = int(est.get("estabelecimento_possui_centro_cirurgico")       or 0)
    tem_obstetrico = int(est.get("estabelecimento_possui_centro_obstetrico")      or 0)
    leitos_proxy   = (tem_internacao + tem_cirurgia + tem_obstetrico) * 25

    esfera   = str(est.get("descricao_esfera_administrativa") or "").upper()
    natureza = "Público" if any(x in esfera for x in ("MUNICIPAL","ESTADUAL","FEDERAL"))                else ("Privado" if str(est.get("descricao_natureza_juridica_estabelecimento") or "").startswith("4")
                     else "Público/Filantrópico")

    uf = muni_row.get("uf") or muni_row.get("uf", "")
    if not uf:
        # fallback: estado é "Rio de Janeiro" → pega sigla via codigo_uf
        try:
            from municipalities import _load_municipalities_csv
            ibge = str(muni_row.get("codigo_ibge",""))
            _df = _load_municipalities_csv()
            row = _df[_df["codigo_ibge"] == ibge]
            uf = row["uf"].iloc[0] if not row.empty else ""
        except Exception:
            uf = ""

    return {
        "co_cnes":              str(est.get("codigo_cnes") or ""),
        "co_cnpj":              str(est.get("numero_cnpj") or est.get("numero_cnpj_entidade") or ""),
        "no_razao_social":      (est.get("nome_razao_social") or "").strip().title(),
        "no_fantasia":          (est.get("nome_fantasia") or "").strip().title(),
        "tp_unidade":           tp,
        "ds_tipo_unidade":      type_desc,
        "category":             category,
        "no_logradouro":        (est.get("endereco_estabelecimento") or "").title(),
        "nu_endereco":          est.get("numero_estabelecimento", ""),
        "no_bairro":            (est.get("bairro_estabelecimento") or "").title(),
        "co_cep":               est.get("codigo_cep_estabelecimento", ""),
        "municipio_nome":       muni_row.get("nome", ""),
        "uf":                   uf,
        "road_km":              round(float(muni_row.get("road_km") or 0), 1),
        "duration_text":        muni_row.get("duration_text", ""),
        "latitude":             lat,
        "longitude":            lng,
        "nu_telefone_cnes":     est.get("numero_telefone_estabelecimento", "") or "",
        "nu_telefone_google":   "",   # preenchido em pós-processamento
        "no_email":             est.get("endereco_email_estabelecimento", "") or "",
        "qt_leito_internacao":  leitos_proxy,
        "qt_leito_sus":         tem_internacao * 25,
        "tem_cirurgia":         _yn(tem_cirurgia),
        "tem_obstetrico":       _yn(tem_obstetrico),
        "atend_ambulatorial":   _yn(est.get("estabelecimento_possui_atendimento_ambulatorial") or 0),
        "atend_sus":            _yn_str(est.get("estabelecimento_faz_atendimento_ambulatorial_sus", "")),
        "tp_gestao":            _dec_gestao(est.get("tipo_gestao", "")),
        "natureza_juridica":    _dec_nat(est.get("descricao_natureza_juridica_estabelecimento", "")),
        "turno_atendimento":    (est.get("descricao_turno_atendimento") or "").replace("ATENDIMENTO ", "").title(),
        "dt_atualizacao":       est.get("data_atualizacao", ""),
    }


def _safe_int(val) -> Optional[int]:
    try:
        return int(val) if val not in (None, "", "None") else 0
    except Exception:
        return 0


# ── Ponto de entrada principal ────────────────────────────────────────────────

def _worker(args):
    muni, only_relevant = args
    co = municipality_ibge_to_cnes_code(str(muni.get("codigo_ibge", "")))
    if not co or co == "nan":
        return []
    rows = []
    for est in _fetch_cnes_municipality(co):
        n = _normalize_establishment(est, muni)
        if only_relevant and n.get("tp_unidade", 0) not in HIGH_COST_RELEVANT_TYPES:
            continue
        rows.append(n)
    return rows


def get_establishments_for_municipalities(
    municipalities: pd.DataFrame,
    only_relevant: bool = False,
    progress_bar=None,
    progress_text_slot=None,
) -> pd.DataFrame:
    """Busca CNES em paralelo (5 workers) e enriquece com telefone Google."""
    all_rows: List[Dict] = []
    total = len(municipalities)
    done  = 0

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_worker, (row, only_relevant)): row
                   for _, row in municipalities.iterrows()}
        for fut in as_completed(futures):
            done += 1
            if progress_bar:   progress_bar.progress(done / total)
            if progress_text_slot:
                progress_text_slot.text(f"🏥 CNES: {done}/{total} municípios")
            try:
                all_rows.extend(fut.result())
            except Exception:
                pass

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["score_potencial"] = df.apply(_calc_score, axis=1)
    df = df.dropna(subset=["latitude", "longitude"])
    df = df.sort_values("score_potencial", ascending=False).reset_index(drop=True)

    # ── Telefone Google: só para alto potencial (score ≥ 40) sem tel CNES ─────
    alto = df["score_potencial"] >= 40
    sem_tel = df["nu_telefone_cnes"].str.strip().eq("") | df["nu_telefone_cnes"].isna()
    targets = df.index[alto | sem_tel].tolist()

    if targets and progress_text_slot:
        progress_text_slot.text(f"📞 Buscando telefones no Google para {len(targets)} estabelecimentos…")

    def _phone_worker(idx):
        row = df.loc[idx]
        nome = row.get("no_fantasia") or row.get("no_razao_social") or ""
        return idx, _get_google_phone(nome, row.get("municipio_nome",""), row.get("category",""))

    with ThreadPoolExecutor(max_workers=5) as ex:
        for idx, phone in ex.map(_phone_worker, targets):
            df.at[idx, "nu_telefone_google"] = phone

    return df


# ── Estatísticas resumidas ────────────────────────────────────────────────────

def summarize_establishments(df: pd.DataFrame) -> Dict:
    """Retorna dicionário com métricas resumidas para o dashboard."""
    if df.empty:
        return {}

    return {
        "total":            len(df),
        "municipios":       df["municipio_nome"].nunique(),
        "hospitais":        (df["category"] == "hospital").sum(),
        "clinicas":         (df["category"] == "clinica").sum(),
        "upas":             (df["category"] == "upa").sum(),
        "farmacias":        (df["category"] == "farmacia").sum(),
        "ubs":              (df["category"] == "ubs").sum(),
        "outros":           (df["category"] == "outro").sum(),
        "total_leitos":     int(df["qt_leito_internacao"].sum()),
        "total_leitos_sus": int(df["qt_leito_sus"].sum()),
        "alto_potencial":   (df["score_potencial"] >= 40).sum(),
        "score_medio":      round(df["score_potencial"].mean(), 1),
    }