"""
doencas_raras.py — Base de dados curada de doenças raras e alto custo
Fonte: PCDT/CEAF (Ministério da Saúde), RENAME, dados epidemiológicos publicados
Preços removidos — referência de mercado via ANVISA/CMED.
"""

import requests
import zipfile
import streamlit as st
import pandas as pd
import folium
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
from config import CNES_BASE_URL


# ── Importação lazy para evitar circularidade ─────────────────────────────────
def _get_tabnet_distribution(cid: str) -> dict:
    try:
        from tabnet_client import get_disease_distribution
        return get_disease_distribution(cid)
    except Exception:
        return {}


# ── Base curada: doenças raras com medicamentos de alto custo ─────────────────

DOENCAS = {
    "Atrofia Muscular Espinhal (AME)": {
        "cids": "G12.0, G12.1",
        "categoria": "Neurológica / Hereditária",
        "prevalencia_100k": 1.0,
        "estimativa_br": 2100,
        "judicializacao": "Muito Alta",
        "medicamentos": [
            {"nome": "Nusinersena (Spinraza)", "via": "SUS/Judicial"},
            {"nome": "Risdiplam (Evrysdi)",    "via": "SUS/Judicial"},
            {"nome": "Onasemnogene (Zolgensma)","via": "Judicial"},
        ],
        "estados_pct": {"SP":34,"MG":13,"RJ":10,"RS":8,"PR":6,"SC":4,"BA":4,"outros":21},
        "perfil": "Doença hereditária autossômica recessiva. 1:6.000 nascidos vivos. "
                  "Alta demanda judicial especialmente para Zolgensma (dose única, >R$5M). "
                  "Sul e Sudeste concentram diagnóstico por infraestrutura.",
    },
    "Esclerose Múltipla": {
        "cids": "G35",
        "categoria": "Neurológica / Autoimune",
        "prevalencia_100k": 15.0,
        "estimativa_br": 40_000,
        "judicializacao": "Alta",
        "medicamentos": [
            {"nome": "Fingolimode (Gilenya)",   "via": "CEAF/Judicial"},
            {"nome": "Natalizumabe (Tysabri)",  "via": "CEAF/Judicial"},
            {"nome": "Ocrelizumabe (Ocrevus)",  "via": "Judicial"},
            {"nome": "Alemtuzumabe (Lemtrada)", "via": "Judicial"},
        ],
        "estados_pct": {"SP":28,"RS":14,"PR":9,"SC":8,"RJ":10,"MG":9,"outros":22},
        "perfil": "Maior prevalência no Sul (ascendência europeia). Mulheres 2:1. "
                  "Medicamentos de 2ª linha são quase todos via ação judicial.",
    },
    "Artrite Reumatoide (Biológicos)": {
        "cids": "M05, M06",
        "categoria": "Reumatológica / Autoimune",
        "prevalencia_100k": 500.0,
        "estimativa_br": 1_300_000,
        "judicializacao": "Alta",
        "medicamentos": [
            {"nome": "Adalimumabe (Humira)",    "via": "CEAF"},
            {"nome": "Etanercepte (Enbrel)",    "via": "CEAF"},
            {"nome": "Infliximabe (Remicade)",  "via": "CEAF"},
            {"nome": "Abatacepte (Orencia)",    "via": "CEAF/Judicial"},
            {"nome": "Secuquinumabe (Cosentyx)","via": "Judicial"},
        ],
        "estados_pct": {"SP":32,"MG":11,"RJ":10,"RS":7,"PR":7,"SC":4,"BA":5,"outros":24},
        "perfil": "Volume altíssimo. Pacientes resistentes a DMARDs clássicos entram no CEAF ou via judicial. "
                  "Maior mercado em volume de unidades para distribuidoras.",
    },
    "Doença de Crohn / Retocolite": {
        "cids": "K50, K51",
        "categoria": "Gastroenterológica / Autoimune",
        "prevalencia_100k": 20.0,
        "estimativa_br": 50_000,
        "judicializacao": "Alta",
        "medicamentos": [
            {"nome": "Adalimumabe (Humira)",     "via": "CEAF/Judicial"},
            {"nome": "Infliximabe (Remicade)",   "via": "CEAF/Judicial"},
            {"nome": "Vedolizumabe (Entyvio)",   "via": "Judicial"},
            {"nome": "Ustekinumabe (Stelara)",   "via": "Judicial"},
        ],
        "estados_pct": {"SP":35,"RJ":12,"MG":10,"RS":8,"PR":7,"SC":5,"outros":23},
        "perfil": "Crescimento acelerado de diagnósticos. Alta taxa de falha ao 1º biológico gera "
                  "demanda contínua por 2ª e 3ª linha, quase sempre via judicial.",
    },
    "Hipertensão Arterial Pulmonar (HAP)": {
        "cids": "I27.0",
        "categoria": "Cardiovascular / Rara",
        "prevalencia_100k": 1.5,
        "estimativa_br": 3_200,
        "judicializacao": "Muito Alta",
        "medicamentos": [
            {"nome": "Bosentana (Tracleer)",    "via": "CEAF/Judicial"},
            {"nome": "Ambrisentana (Volibris)", "via": "Judicial"},
            {"nome": "Riociguate (Adempas)",    "via": "Judicial"},
            {"nome": "Macitentan (Opsumit)",    "via": "Judicial"},
            {"nome": "Selexipag (Uptravi)",     "via": "Judicial"},
        ],
        "estados_pct": {"SP":40,"RJ":12,"MG":10,"RS":6,"PR":5,"SC":4,"outros":23},
        "perfil": "Altíssimo custo unitário. Quase 100% via judicial ou importação. "
                  "Pequeno volume, margens elevadíssimas. Concentrado em centros de referência.",
    },
    "Doença de Gaucher": {
        "cids": "E75.2",
        "categoria": "Metabólica / Hereditária",
        "prevalencia_100k": 0.5,
        "estimativa_br": 1_050,
        "judicializacao": "Alta",
        "medicamentos": [
            {"nome": "Imiglucerase (Cerezyme)",    "via": "SUS/Judicial"},
            {"nome": "Velaglucerase alfa (VPRIV)", "via": "SUS/Judicial"},
            {"nome": "Eliglustate (Cerdelga)",     "via": "Judicial"},
        ],
        "estados_pct": {"SP":40,"RJ":12,"MG":9,"RS":6,"PR":5,"BA":4,"outros":24},
        "perfil": "Maior prevalência em descendentes de judeus Ashkenazi (SP/RJ). "
                  "Tratamento enzimático de alto custo e uso contínuo.",
    },
    "Doença de Fabry": {
        "cids": "E75.2",
        "categoria": "Metabólica / Hereditária",
        "prevalencia_100k": 0.3,
        "estimativa_br": 630,
        "judicializacao": "Alta",
        "medicamentos": [
            {"nome": "Agalsidase alfa (Replagal)", "via": "SUS/Judicial"},
            {"nome": "Agalsidase beta (Fabrazyme)","via": "SUS/Judicial"},
            {"nome": "Migalastate (Galafold)",     "via": "Judicial"},
        ],
        "estados_pct": {"SP":38,"MG":12,"RJ":10,"RS":7,"PR":5,"outros":28},
        "perfil": "Ligada ao X. Alta subdiagnose. Diagnóstico tardio = progressão de dano renal/cardíaco. "
                  "Pacientes identificados por cardiologistas e nefrologistas.",
    },
    "Fibrose Cística": {
        "cids": "E84",
        "categoria": "Respiratória / Hereditária",
        "prevalencia_100k": 3.0,
        "estimativa_br": 5_000,
        "judicializacao": "Muito Alta",
        "medicamentos": [
            {"nome": "Ivacaftor (Kalydeco)",                        "via": "Judicial"},
            {"nome": "Lumacaftor+Ivacaftor (Orkambi)",             "via": "Judicial"},
            {"nome": "Tezacaftor+Ivacaftor (Symdeko)",             "via": "Judicial"},
            {"nome": "Elexacaftor+Tezacaftor+Ivacaftor (Trikafta)","via": "Judicial"},
        ],
        "estados_pct": {"SP":32,"RS":13,"SC":10,"PR":9,"MG":9,"RJ":7,"outros":20},
        "perfil": "Prevalência maior no Sul (ascendência europeia). Moduladores de CFTR são o "
                  "maior litígio judicial em saúde por valor individual no Brasil atual.",
    },
    "Hemofilia A e B": {
        "cids": "D66, D67",
        "categoria": "Hematológica / Hereditária",
        "prevalencia_100k": 10.0,
        "estimativa_br": 21_000,
        "judicializacao": "Média",
        "medicamentos": [
            {"nome": "Fator VIII recombinante",          "via": "SUS"},
            {"nome": "Fator IX recombinante",            "via": "SUS"},
            {"nome": "Emicizumabe (Hemlibra)",           "via": "SUS/Judicial"},
            {"nome": "Fator VIII com inibidores (FEIBA)","via": "SUS/Judicial"},
        ],
        "estados_pct": {"SP":35,"MG":12,"RJ":10,"RS":7,"PR":6,"BA":5,"outros":25},
        "perfil": "Majoritariamente atendido pelo SUS (hemocentros). Casos com inibidores e "
                  "emicizumabe geram alta demanda judicial. Mercado estável e previsível.",
    },
    "Psoríase Grave / Espondilite Anquilosante": {
        "cids": "L40.0, M45",
        "categoria": "Dermatológica/Reumatológica / Autoimune",
        "prevalencia_100k": 30.0,
        "estimativa_br": 80_000,
        "judicializacao": "Alta",
        "medicamentos": [
            {"nome": "Secuquinumabe (Cosentyx)", "via": "CEAF/Judicial"},
            {"nome": "Ixequizumabe (Taltz)",     "via": "Judicial"},
            {"nome": "Guselcumabe (Tremfya)",    "via": "Judicial"},
            {"nome": "Risanquizumabe (Skyrizi)", "via": "Judicial"},
        ],
        "estados_pct": {"SP":30,"RJ":12,"MG":10,"RS":9,"PR":8,"SC":5,"outros":26},
        "perfil": "Mercado em expansão por novos biológicos. Dermatologistas e reumatologistas "
                  "são os principais prescritores. Pacientes jovens = tratamento de longa duração.",
    },
    "Leucemia Mieloide Crônica (LMC)": {
        "cids": "C91.1",
        "categoria": "Oncológica / Hematológica",
        "prevalencia_100k": 5.0,
        "estimativa_br": 13_000,
        "judicializacao": "Alta",
        "medicamentos": [
            {"nome": "Imatinibe (Glivec)",   "via": "SUS/CEAF"},
            {"nome": "Dasatinibe (Sprycel)", "via": "CEAF/Judicial"},
            {"nome": "Nilotinibe (Tasigna)", "via": "CEAF/Judicial"},
            {"nome": "Ponatinibe (Iclusig)", "via": "Judicial"},
        ],
        "estados_pct": {"SP":33,"MG":11,"RJ":10,"RS":7,"PR":6,"BA":5,"outros":28},
        "perfil": "Imatinibe no SUS garante 1ª linha. Resistência ou mutação T315I gera "
                  "demanda judicial para 2ª e 3ª geração. Tratamento crônico e contínuo.",
    },
    "Mucopolissacaridoses (MPS)": {
        "cids": "E76.0, E76.1, E76.2",
        "categoria": "Metabólica / Hereditária",
        "prevalencia_100k": 0.4,
        "estimativa_br": 840,
        "judicializacao": "Alta",
        "medicamentos": [
            {"nome": "Laronidase (Aldurazyme) — MPS I", "via": "SUS/Judicial"},
            {"nome": "Idursulfase (Elaprase) — MPS II",  "via": "SUS/Judicial"},
            {"nome": "Galsulfase (Naglazyme) — MPS VI",  "via": "SUS/Judicial"},
        ],
        "estados_pct": {"SP":38,"MG":11,"RJ":9,"RS":7,"PR":6,"BA":5,"outros":24},
        "perfil": "Tratamentos mais caros do CEAF. Crianças. Alta demanda judicial. "
                  "Centros de referência concentrados em SP e capitais.",
    },
}

# ── Dados de judicialização em saúde por estado ───────────────────────────────
# Fonte: CNJ "Judicialização da Saúde" 2020, INSPER 2019, CONASS 2021
# Usado como fallback quando DataJud não responde

JUDICIALIZACAO_ESTADOS = {
    "SP": {"processos_ano": 45_000, "pct_medicamentos": 42, "pct_vitoria_paciente": 72, "custo_total_mi": 1_800},
    "MG": {"processos_ano": 22_000, "pct_medicamentos": 38, "pct_vitoria_paciente": 68, "custo_total_mi": 650},
    "RJ": {"processos_ano": 18_000, "pct_medicamentos": 45, "pct_vitoria_paciente": 70, "custo_total_mi": 720},
    "RS": {"processos_ano": 15_000, "pct_medicamentos": 55, "pct_vitoria_paciente": 74, "custo_total_mi": 850},
    "SC": {"processos_ano": 9_000,  "pct_medicamentos": 52, "pct_vitoria_paciente": 75, "custo_total_mi": 480},
    "PR": {"processos_ano": 11_000, "pct_medicamentos": 48, "pct_vitoria_paciente": 71, "custo_total_mi": 530},
    "BA": {"processos_ano": 8_000,  "pct_medicamentos": 35, "pct_vitoria_paciente": 62, "custo_total_mi": 280},
    "CE": {"processos_ano": 6_000,  "pct_medicamentos": 33, "pct_vitoria_paciente": 60, "custo_total_mi": 190},
    "PE": {"processos_ano": 5_500,  "pct_medicamentos": 36, "pct_vitoria_paciente": 63, "custo_total_mi": 200},
    "GO": {"processos_ano": 7_000,  "pct_medicamentos": 40, "pct_vitoria_paciente": 66, "custo_total_mi": 250},
    "DF": {"processos_ano": 5_000,  "pct_medicamentos": 44, "pct_vitoria_paciente": 69, "custo_total_mi": 310},
    "AM": {"processos_ano": 2_500,  "pct_medicamentos": 30, "pct_vitoria_paciente": 55, "custo_total_mi": 80},
    "PA": {"processos_ano": 3_000,  "pct_medicamentos": 28, "pct_vitoria_paciente": 52, "custo_total_mi": 90},
    "MT": {"processos_ano": 3_500,  "pct_medicamentos": 41, "pct_vitoria_paciente": 65, "custo_total_mi": 130},
    "MS": {"processos_ano": 2_800,  "pct_medicamentos": 43, "pct_vitoria_paciente": 67, "custo_total_mi": 110},
}

# ── Coordenadas dos estados para o mapa ──────────────────────────────────────
ESTADO_COORDS = {
    "AC":(-9.0,-70.8),"AL":(-9.5,-36.8),"AM":(-4.0,-65.0),"AP":(1.4,-51.8),
    "BA":(-12.5,-41.7),"CE":(-5.5,-39.3),"DF":(-15.8,-47.9),"ES":(-19.2,-40.3),
    "GO":(-15.8,-49.6),"MA":(-5.4,-45.4),"MG":(-18.1,-44.4),"MS":(-20.5,-54.5),
    "MT":(-12.6,-55.9),"PA":(-3.8,-52.5),"PB":(-7.1,-36.8),"PE":(-8.3,-37.9),
    "PI":(-6.6,-42.3),"PR":(-24.6,-51.4),"RJ":(-22.3,-42.8),"RN":(-5.8,-36.5),
    "RO":(-11.5,-63.6),"RR":(1.9,-61.2),"RS":(-30.2,-53.2),"SC":(-27.3,-50.2),
    "SE":(-10.6,-37.4),"SP":(-22.2,-48.5),"TO":(-10.2,-48.3),
}

# ── Mapeamento doença → serviços CNES ────────────────────────────────────────
# Fonte: Tabela de Serviços/Classificação CNES + SIGTAP

DOENCA_SERVICOS_CNES = {
    "Atrofia Muscular Espinhal (AME)": {
        "servicos":       ["145"],
        "classificacoes": ["006"],
        "palavras_chave": ["neurologia", "neuromuscular", "pediatria"],
    },
    "Esclerose Múltipla": {
        "servicos":       ["145"],
        "classificacoes": ["003"],
        "palavras_chave": ["neurologia", "esclerose"],
    },
    "Artrite Reumatoide (Biológicos)": {
        "servicos":       ["135"],
        "classificacoes": ["001"],
        "palavras_chave": ["reumatologia", "reumatismo"],
    },
    "Hipertensão Arterial Pulmonar (HAP)": {
        "servicos":       ["115"],
        "classificacoes": ["003", "009"],
        "palavras_chave": ["cardiologia", "pneumologia", "hipertensão pulmonar"],
    },
    "Fibrose Cística": {
        "servicos":       ["140"],
        "classificacoes": ["001", "002"],
        "palavras_chave": ["pneumologia", "fibrose", "cística"],
    },
    "Hemofilia A e B": {
        "servicos":       ["125"],
        "classificacoes": ["001"],
        "palavras_chave": ["hematologia", "hemofilia", "coagulopatia"],
    },
    "Doença de Gaucher": {
        "servicos":       ["125", "145"],
        "classificacoes": [],
        "palavras_chave": ["hematologia", "neurologia", "gaucher", "metabólica", "erros inatos"],
    },
    "Mucopolissacaridoses (MPS)": {
        "servicos":       ["125", "145"],
        "classificacoes": [],
        "palavras_chave": ["neurologia", "metabólica", "genética", "mucopolissacaridose"],
    },
    "Leucemia Mieloide Crônica (LMC)": {
        "servicos":       ["125"],
        "classificacoes": ["003"],
        "palavras_chave": ["hematologia", "oncologia", "onco-hematologia"],
    },
    "Doença de Crohn / Retocolite": {
        "servicos":       ["110"],
        "classificacoes": ["001"],
        "palavras_chave": ["gastroenterologia", "coloproctologia"],
    },
    "Psoríase Grave / Espondilite Anquilosante": {
        "servicos":       ["135", "130"],
        "classificacoes": [],
        "palavras_chave": ["dermatologia", "reumatologia", "psoríase"],
    },
}

# ── Mapeamento doença → prefixos ATC (ANVISA) ────────────────────────────────
# ATC classifica medicamentos por sistema anatômico/grupo terapêutico

DOENCA_ATC_PREFIXOS = {
    "Atrofia Muscular Espinhal (AME)":           ["M09AX", "N07XX"],
    "Esclerose Múltipla":                        ["L03AX", "L04AX"],
    "Artrite Reumatoide (Biológicos)":           ["L04AB", "L04AC", "L04AX"],
    "Doença de Crohn / Retocolite":              ["L04AB", "A07EC"],
    "Hipertensão Arterial Pulmonar (HAP)":       ["C02KX"],
    "Doença de Gaucher":                         ["A16AB"],
    "Doença de Fabry":                           ["A16AB"],
    "Fibrose Cística":                           ["R07AX"],
    "Hemofilia A e B":                           ["B02BD"],
    "Psoríase Grave / Espondilite Anquilosante": ["L04AC", "L04AB"],
    "Leucemia Mieloide Crônica (LMC)":           ["L01EA", "L01XE"],
    "Mucopolissacaridoses (MPS)":                ["A16AB"],
}


# ── Funções auxiliares ────────────────────────────────────────────────────────

def _get_estados_pct(disease_name: str):
    """Retorna (estados_pct_dict, fonte_str) — tenta TabNet antes do fallback."""
    d = DOENCAS.get(disease_name, {})
    cid = d.get("cids", "").split(",")[0].strip()[:3]
    real_dist = _get_tabnet_distribution(cid) if cid else {}
    if real_dist:
        return real_dist, "DATASUS/SIA"
    return d.get("estados_pct", {}), "estimativa epidemiológica"


@st.cache_data(ttl=86400, show_spinner=False)
def _load_anvisa_medicamentos() -> pd.DataFrame:
    """
    Baixa e parseia o dataset de medicamentos registrados na ANVISA.
    Cache de 24h. ~50 MB comprimido.
    """
    try:
        r = requests.get(
            "https://dados.anvisa.gov.br/dados/MEDICAMENTOS.zip",
            timeout=60,
            stream=True,
        )
        if r.status_code != 200:
            return pd.DataFrame()

        with zipfile.ZipFile(BytesIO(r.content)) as z:
            csv_name = [f for f in z.namelist() if f.endswith(".csv")][0]
            with z.open(csv_name) as f:
                df = pd.read_csv(f, sep=";", encoding="latin1", low_memory=False)

        # Mantém apenas registros ativos
        if "SITUACAO_REGISTRO" in df.columns:
            df = df[
                df["SITUACAO_REGISTRO"]
                .astype(str)
                .str.upper()
                .str.contains("ATIVO|VÁLIDO|VALIDO", na=False)
            ]
        return df

    except Exception:
        return pd.DataFrame()


def get_medicamentos_por_doenca(disease_name: str) -> list:
    """
    Retorna lista de dicts {nome, principio_ativo, via} para a doença.
    Fonte primária: ANVISA dados abertos (filtro por ATC).
    Fallback: lista hardcoded em DOENCAS (sem preços).
    """
    atc_prefixos = DOENCA_ATC_PREFIXOS.get(disease_name, [])

    if atc_prefixos:
        df = _load_anvisa_medicamentos()
        if not df.empty and "CODIGO_ATC" in df.columns:
            mask = df["CODIGO_ATC"].astype(str).str.startswith(tuple(atc_prefixos))
            df_f = df[mask].copy()
            if not df_f.empty:
                nome_col = "NOME_PRODUTO" if "NOME_PRODUTO" in df_f.columns else df_f.columns[0]
                pa_col   = "PRINCIPIO_ATIVO" if "PRINCIPIO_ATIVO" in df_f.columns else None
                result   = []
                seen     = set()
                for _, row in df_f.iterrows():
                    nome = str(row.get(nome_col, "")).title()
                    pa   = str(row.get(pa_col, "")).title() if pa_col else ""
                    key  = pa or nome
                    if key and key not in seen and key.lower() != "nan":
                        seen.add(key)
                        result.append({
                            "nome":            nome,
                            "principio_ativo": pa,
                            "via":             "SUS/CEAF/Judicial",
                        })
                if result:
                    return result[:20]

    # Fallback: dados hardcoded (sem preços)
    d = DOENCAS.get(disease_name, {})
    return [
        {"nome": m["nome"], "principio_ativo": "", "via": m.get("via", "")}
        for m in d.get("medicamentos", [])
    ]


@st.cache_data(ttl=3600, show_spinner=False)
def buscar_estabelecimentos_por_doenca(
    disease_name: str,
    uf: str,
    municipio_codigo: str = None,
) -> pd.DataFrame:
    """
    Busca estabelecimentos no CNES que oferecem serviços relacionados à doença.
    Retorna DataFrame com: co_cnes, nome, municipio, uf, latitude, longitude, servico.
    """
    config_ = DOENCA_SERVICOS_CNES.get(disease_name, {})
    servicos_buscar = config_.get("servicos", [])

    if not servicos_buscar:
        return pd.DataFrame()

    rows = []
    for cod_servico in servicos_buscar:
        params = {
            "codigo_uf":       uf,
            "codigo_servico":  cod_servico,
            "limit":           50,
            "offset":          0,
        }
        if municipio_codigo:
            params["codigo_municipio"] = municipio_codigo

        try:
            r = requests.get(
                f"{CNES_BASE_URL}/estabelecimentos",
                params=params,
                timeout=15,
            )
            if r.status_code == 200:
                for est in r.json().get("estabelecimentos", []):
                    rows.append({
                        "co_cnes":   est.get("codigo_cnes", ""),
                        "nome":      (est.get("nome_fantasia") or est.get("nome_razao_social", "")).title(),
                        "municipio": est.get("nome_municipio", ""),
                        "uf":        uf,
                        "latitude":  est.get("latitude_estabelecimento_decimo_grau"),
                        "longitude": est.get("longitude_estabelecimento_decimo_grau"),
                        "servico":   cod_servico,
                    })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).drop_duplicates(subset=["co_cnes"])
    return df.sort_values("nome").reset_index(drop=True)


# ── Funções principais ────────────────────────────────────────────────────────

def get_disease_df() -> pd.DataFrame:
    rows = []
    for nome, d in DOENCAS.items():
        med_nomes = " / ".join(m["nome"] for m in d["medicamentos"][:3])
        rows.append({
            "Doença":                  nome,
            "CIDs":                    d["cids"],
            "Categoria":               d["categoria"],
            "Estimativa BR":           d["estimativa_br"],
            "Prev./100k":              d["prevalencia_100k"],
            "Judicialização":          d["judicializacao"],
            "Principais Medicamentos": med_nomes,
        })
    return pd.DataFrame(rows).sort_values("Estimativa BR", ascending=False)


def get_state_concentration(disease_name: str):
    """Retorna (DataFrame, fonte_str) com concentração por estado."""
    estados_pct, fonte = _get_estados_pct(disease_name)
    estimativa = DOENCAS.get(disease_name, {}).get("estimativa_br", 0)
    rows = []
    for uf, pct in estados_pct.items():
        if uf == "outros":
            continue
        rows.append({
            "UF":                  uf,
            "% Nacional":          pct,
            "Estimativa Pacientes": int(estimativa * pct / 100),
        })
    return pd.DataFrame(rows).sort_values("% Nacional", ascending=False), fonte


def make_state_map(disease_name: str) -> folium.Map:
    m = folium.Map(location=[-15, -50], zoom_start=4, tiles=None)
    folium.TileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satélite", max_zoom=19,
    ).add_to(m)
    folium.TileLayer(
        "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="CartoDB", name="Mapa",
    ).add_to(m)

    estados_pct, _ = _get_estados_pct(disease_name)
    estimativa = DOENCAS.get(disease_name, {}).get("estimativa_br", 0)

    for uf, pct in estados_pct.items():
        if uf == "outros" or uf not in ESTADO_COORDS:
            continue
        lat, lng  = ESTADO_COORDS[uf]
        pacientes = int(estimativa * pct / 100)
        radius    = max(20, min(80, int(pct * 5)))
        color     = "#D32F2F" if pct >= 15 else ("#F57C00" if pct >= 8 else "#1976D2")
        folium.CircleMarker(
            location=[lat, lng], radius=radius,
            color=color, fill=True, fill_color=color, fill_opacity=0.7,
            tooltip=folium.Tooltip(
                f"<b>{uf}</b><br>{pct}% dos pacientes<br>~{pacientes:,} pacientes estimados",
                sticky=True,
            ),
        ).add_to(m)
        folium.Marker(
            location=[lat, lng], icon=folium.DivIcon(
                html=f'<div style="font-weight:bold;color:white;font-size:11px;'
                     f'text-align:center;text-shadow:1px 1px 2px black">{uf}</div>',
                icon_size=(35, 20), icon_anchor=(17, 10),
            ),
        ).add_to(m)

    folium.LayerControl().add_to(m)
    return m
