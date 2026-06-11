"""
datajud_client.py
"""

import requests
import pandas as pd
import streamlit as st
from concurrent.futures import ThreadPoolExecutor

DATAJUD_BASE = "https://api-publica.datajud.cnj.jus.br"
DATAJUD_KEY  = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="

TRIBUNAIS = {
    "SP": "api_publica_tjsp", "MG": "api_publica_tjmg",
    "RJ": "api_publica_tjrj", "RS": "api_publica_tjrs",
    "SC": "api_publica_tjsc", "PR": "api_publica_tjpr",
    "BA": "api_publica_tjba", "CE": "api_publica_tjce",
    "PE": "api_publica_tjpe", "GO": "api_publica_tjgo",
    "DF": "api_publica_tjdft","MT": "api_publica_tjmt",
    "MS": "api_publica_tjms", "PA": "api_publica_tjpa",
    "AM": "api_publica_tjam",
}

FALLBACK = {
    "SP": {"processos_medicamentos": 45000, "processos_saude_total": 107000, "pct_medicamentos": 42},
    "MG": {"processos_medicamentos": 22000, "processos_saude_total": 58000,  "pct_medicamentos": 38},
    "RJ": {"processos_medicamentos": 18000, "processos_saude_total": 40000,  "pct_medicamentos": 45},
    "RS": {"processos_medicamentos": 15000, "processos_saude_total": 27000,  "pct_medicamentos": 55},
    "SC": {"processos_medicamentos": 9000,  "processos_saude_total": 17000,  "pct_medicamentos": 52},
    "PR": {"processos_medicamentos": 11000, "processos_saude_total": 23000,  "pct_medicamentos": 48},
    "BA": {"processos_medicamentos": 8000,  "processos_saude_total": 23000,  "pct_medicamentos": 35},
    "CE": {"processos_medicamentos": 6000,  "processos_saude_total": 18000,  "pct_medicamentos": 33},
    "PE": {"processos_medicamentos": 5500,  "processos_saude_total": 15000,  "pct_medicamentos": 36},
    "GO": {"processos_medicamentos": 7000,  "processos_saude_total": 18000,  "pct_medicamentos": 40},
    "DF": {"processos_medicamentos": 5000,  "processos_saude_total": 11000,  "pct_medicamentos": 44},
    "MT": {"processos_medicamentos": 3500,  "processos_saude_total": 8500,   "pct_medicamentos": 41},
    "MS": {"processos_medicamentos": 2800,  "processos_saude_total": 6500,   "pct_medicamentos": 43},
    "PA": {"processos_medicamentos": 3000,  "processos_saude_total": 11000,  "pct_medicamentos": 28},
    "AM": {"processos_medicamentos": 2500,  "processos_saude_total": 8000,   "pct_medicamentos": 30},
}


def _hdr():
    return {"Authorization": f"APIKey {DATAJUD_KEY}", "Content-Type": "application/json"}


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_datajud_saude(uf: str) -> dict:
    tribunal = TRIBUNAIS.get(uf)
    if not tribunal:
        return {**FALLBACK.get(uf, {}), "fonte": "Relatórios CNJ/INSPER 2019-2021"}

    url = f"{DATAJUD_BASE}/{tribunal}/_search"

    queries_med = [
        {
            "size": 0,
            "query": {"nested": {"path": "assuntos", "query": {"bool": {"should": [
                {"match": {"assuntos.nome": "medicamento"}},
                {"match": {"assuntos.nome": "fornecimento de medicamento"}},
            ]}}}},
            "aggs": {"por_ano": {"date_histogram": {
                "field": "dataAjuizamento",
                "calendar_interval": "year",
                "format": "yyyy",
            }}},
        },
        {
            "size": 0,
            "query": {"match": {"assuntos.nome": "medicamento"}},
        },
        {
            "size": 0,
            "query": {"bool": {"should": [
                {"term": {"assuntos.codigo": "14018"}},
                {"term": {"assuntos.codigo": 14018}},
            ]}},
        },
    ]

    query_saude = {
        "size": 0,
        "query": {"bool": {"should": [
            {"match": {"assuntos.nome": "saude"}},
            {"match": {"assuntos.nome": "saúde"}},
        ]}},
    }

    try:
        total_med = 0
        anos = []
        for q in queries_med:
            r = requests.post(url, headers=_hdr(), json=q, timeout=12)
            if r.status_code == 200:
                d = r.json()
                hits = d.get("hits", {}).get("total", {}).get("value", 0)
                if hits > 0:
                    total_med = hits
                    buckets = d.get("aggregations", {}).get("por_ano", {}).get("buckets", [])
                    anos = [{"ano": b["key_as_string"], "total": b["doc_count"]} for b in buckets]
                    break

        total_saude = 0
        r2 = requests.post(url, headers=_hdr(), json=query_saude, timeout=12)
        if r2.status_code == 200:
            total_saude = r2.json().get("hits", {}).get("total", {}).get("value", 0)

        if total_med < 50 and total_saude < 50:
            return {
                **FALLBACK.get(uf, {}),
                "fonte": "Relatórios CNJ/INSPER 2019-2021 (DataJud sem dados)",
            }

        pct = round((total_med / total_saude * 100), 1) if total_saude > 0 else 0
        return {
            "processos_medicamentos": total_med,
            "processos_saude_total":  total_saude,
            "pct_medicamentos":       pct,
            "serie_historica":        anos,
            "fonte":                  "DataJud/CNJ — tempo real",
        }

    except Exception:
        return {
            **FALLBACK.get(uf, {}),
            "fonte": "Relatórios CNJ/INSPER 2019-2021 (DataJud indisponível)",
        }


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_all_states() -> dict:
    results = {}

    def _one(uf):
        return uf, fetch_datajud_saude(uf)

    with ThreadPoolExecutor(max_workers=5) as ex:
        for uf, data in ex.map(_one, list(TRIBUNAIS.keys())):
            results[uf] = data

    return results


def build_judicial_df(datajud_data: dict):
    rows = []
    fonte = "DataJud/CNJ"
    for uf, d in datajud_data.items():
        rows.append({
            "Estado":                 uf,
            "Processos Medicamentos": d.get("processos_medicamentos", 0),
            "Total Saúde":            d.get("processos_saude_total", 0),
            "% Medicamentos":         d.get("pct_medicamentos", 0),
        })
        if "Relatórios" in d.get("fonte", ""):
            fonte = "Relatórios CNJ/INSPER 2019-2021"
    df = pd.DataFrame(rows).sort_values("Processos Medicamentos", ascending=False)
    return df, fonte
