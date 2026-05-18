"""
cnes.py — Integração com a API pública do DATASUS/CNES
  - Busca estabelecimentos por município (com paginação)
  - Enriquece com dados de leitos, tipo, gestão
  - Calcula score de potencial de medicamentos de alto custo
  - Cache por município para evitar requisições repetidas
"""

import time
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
)
from municipalities import municipality_ibge_to_cnes_code


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

    for page in range(max_pages):
        params = {
            "codigo_municipio": co_municipio,
            "limit": CNES_PAGE_SIZE,
            "offset": page * CNES_PAGE_SIZE,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("estabelecimentos", [])
            if not items:          # última página — para apenas quando a API retorna []
                break
            all_items.extend(items)
            time.sleep(0.05)
        except Exception:
            break

    # Remove duplicatas que podem surgir se a API retornar sobreposição entre páginas
    seen = set()
    unique = []
    for item in all_items:
        key = item.get("codigo_cnes")
        if key and key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


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

    # 3. Serviços adicionais (0–10)
    score += min(10, (int(row.get("tem_cirurgia") or 0) +
                      int(row.get("tem_obstetrico") or 0) +
                      int(row.get("atend_ambulatorial") or 0)) * 3)

    # 4. Gestão: federal/estadual prioriza alta complexidade (0–10)
    gestao = str(row.get("tp_gestao", "")).upper()
    if gestao in ("E", "S"):   # estadual / federal
        score += 10
    elif gestao == "D":         # dupla
        score += 6
    elif gestao == "M":         # municipal
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

    return {
        "co_cnes":             str(est.get("codigo_cnes") or ""),
        "co_cnpj":             str(est.get("numero_cnpj") or est.get("numero_cnpj_entidade") or ""),
        "no_razao_social":     (est.get("nome_razao_social") or "").strip().title(),
        "no_fantasia":         (est.get("nome_fantasia") or "").strip().title(),
        "tp_unidade":          tp,
        "ds_tipo_unidade":     type_desc,
        "category":            category,
        "no_logradouro":       est.get("endereco_estabelecimento", ""),
        "nu_endereco":         est.get("numero_estabelecimento", ""),
        "no_bairro":           est.get("bairro_estabelecimento", ""),
        "co_cep":              est.get("codigo_cep_estabelecimento", ""),
        "municipio_nome":      muni_row.get("nome", ""),
        "uf":                  muni_row.get("uf", ""),
        "road_km":             round(float(muni_row.get("road_km") or 0), 1),
        "duration_text":       muni_row.get("duration_text", ""),
        "latitude":            lat,
        "longitude":           lng,
        "nu_telefone":         est.get("numero_telefone_estabelecimento", ""),
        "no_email":            est.get("endereco_email_estabelecimento", ""),
        "qt_leito_internacao": leitos_proxy,
        "qt_leito_sus":        tem_internacao * 25,
        "tem_cirurgia":        tem_cirurgia,
        "tem_obstetrico":      tem_obstetrico,
        "atend_ambulatorial":  int(est.get("estabelecimento_possui_atendimento_ambulatorial") or 0),
        "atend_sus":           est.get("estabelecimento_faz_atendimento_ambulatorial_sus", ""),
        "tp_gestao":           est.get("tipo_gestao", ""),
        "tp_pfpj":             natureza,
        "ds_natureza_juridica": est.get("descricao_natureza_juridica_estabelecimento", ""),
        "turno_atendimento":   est.get("descricao_turno_atendimento", ""),
        "dt_atualizacao":      est.get("data_atualizacao", ""),
    }


def _safe_int(val) -> Optional[int]:
    try:
        return int(val) if val not in (None, "", "None") else 0
    except Exception:
        return 0


# ── Ponto de entrada principal ────────────────────────────────────────────────

def get_establishments_for_municipalities(
    municipalities: pd.DataFrame,
    only_relevant: bool = False,
    progress_bar=None,
    progress_text_slot=None,
) -> pd.DataFrame:
    """
    Consulta o CNES para todos os municípios do DataFrame.
    Retorna DataFrame consolidado com score de potencial calculado.

    Parâmetros:
      only_relevant: se True, retorna apenas tipos relevantes para alto custo
    """
    all_rows: List[Dict] = []
    total = len(municipalities)

    for i, (_, muni) in enumerate(municipalities.iterrows()):
        if progress_bar is not None:
            progress_bar.progress(i / total)
        if progress_text_slot is not None:
            progress_text_slot.text(
                f"🏥 Consultando CNES: {muni.get('nome', '')} — {i+1}/{total}"
            )

        co_municipio = municipality_ibge_to_cnes_code(str(muni.get("codigo_ibge", "")))
        if not co_municipio:
            continue

        raw_establishments = _fetch_cnes_municipality(co_municipio)

        for est in raw_establishments:
            normalized = _normalize_establishment(est, muni)

            if only_relevant:
                tp = normalized.get("tp_unidade", 0)
                if tp not in HIGH_COST_RELEVANT_TYPES:
                    continue

            all_rows.append(normalized)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)

    # Calcula score de potencial
    df["score_potencial"] = df.apply(_calc_score, axis=1)

    # Remove estabelecimentos sem coordenadas válidas (não aparecem no mapa)
    df = df.dropna(subset=["latitude", "longitude"])

    return df.sort_values("score_potencial", ascending=False).reset_index(drop=True)


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
        "total_leitos":     int(df["qt_leito_internacao"].sum()),
        "total_leitos_sus": int(df["qt_leito_sus"].sum()),
        "alto_potencial":   (df["score_potencial"] >= 30).sum(),
        "score_medio":      round(df["score_potencial"].mean(), 1),
    }