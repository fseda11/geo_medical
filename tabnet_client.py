"""
tabnet_client.py — Cliente DATASUS/TabNet via CGI
Consulta produção ambulatorial SIA/SUS por CID e retorna distribuição por UF.
"""

import requests
import pandas as pd
import streamlit as st
from io import StringIO

TABNET_URL = "https://tabnet.datasus.gov.br/cgi/tabcgi.exe"

# Mapeamento UF nome completo → sigla
_UF_MAP = {
    "Rondônia": "RO", "Acre": "AC", "Amazonas": "AM", "Roraima": "RR",
    "Pará": "PA", "Amapá": "AP", "Tocantins": "TO", "Maranhão": "MA",
    "Piauí": "PI", "Ceará": "CE", "Rio Grande do Norte": "RN",
    "Paraíba": "PB", "Pernambuco": "PE", "Alagoas": "AL", "Sergipe": "SE",
    "Bahia": "BA", "Minas Gerais": "MG", "Espírito Santo": "ES",
    "Rio de Janeiro": "RJ", "São Paulo": "SP", "Paraná": "PR",
    "Santa Catarina": "SC", "Rio Grande do Sul": "RS",
    "Mato Grosso do Sul": "MS", "Mato Grosso": "MT",
    "Goiás": "GO", "Distrito Federal": "DF",
}


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_sia_by_cid(cid_prefix: str, ano_inicio: int = 2022, ano_fim: int = 2023) -> pd.DataFrame:
    """
    Busca produção ambulatorial (SIA) por CID no TabNet.
    Retorna DataFrame com colunas: uf, total.

    O TabNet usa sistema CGI legado. A requisição POST reproduz os parâmetros
    do formulário HTML da consulta SIA/SUS — Produção Ambulatorial por CID.
    URL base: https://tabnet.datasus.gov.br/cgi/tabcgi.exe?sia/cnv/qauf.def
    """
    params = {
        "Linha":        "Unidade_da_Federação",
        "Coluna":       "Não_ativa",
        "Incremento":   "Quantidade_aprovada",
        "Arquivos":     f"qauf{ano_inicio % 100:02d}.dbf",
        "pesqmes1":     "",
        "SMunic":       "TODAS_AS_CATEGORIAS__",
        "SEstado":      "TODAS_AS_CATEGORIAS__",
        "SCID10":       cid_prefix,
        "SFaixaEtaria": "TODAS_AS_CATEGORIAS__",
        "formato":      "table",
        "mostre":       "Mostra",
    }

    try:
        r = requests.post(
            f"{TABNET_URL}?sia/cnv/qauf.def",
            data=params,
            timeout=30,
        )
        if r.status_code != 200:
            return pd.DataFrame(columns=["uf", "total"])

        tables = pd.read_html(StringIO(r.text), header=0)
        if not tables:
            return pd.DataFrame(columns=["uf", "total"])

        df = tables[0].copy()
        df.columns = [str(c).strip() for c in df.columns]

        # Detecta coluna de UF e coluna de valor
        uf_cols = [c for c in df.columns if "federação" in c.lower() or "estado" in c.lower()]
        if not uf_cols:
            return pd.DataFrame(columns=["uf", "total"])
        uf_col = uf_cols[0]

        val_cols = [c for c in df.columns if c != uf_col]
        if not val_cols:
            return pd.DataFrame(columns=["uf", "total"])
        val_col = val_cols[0]

        df = df[[uf_col, val_col]].copy()
        df.columns = ["estado", "total"]
        df = df[
            df["estado"].notna()
            & ~df["estado"].astype(str).str.contains("Total|TOTAL", na=False)
        ]
        df["total"] = (
            pd.to_numeric(
                df["total"].astype(str).str.replace(".", "", regex=False).str.replace(",", "", regex=False),
                errors="coerce",
            ).fillna(0)
        )

        df["uf"] = df["estado"].map(_UF_MAP)
        df = df.dropna(subset=["uf"])
        return df[["uf", "total"]].sort_values("total", ascending=False).reset_index(drop=True)

    except Exception:
        # TabNet é instável — fallback silencioso
        return pd.DataFrame(columns=["uf", "total"])


def get_disease_distribution(cid_prefix: str) -> dict:
    """
    Retorna dict {UF: percentual} para uso em doencas_raras.py.
    Se TabNet falhar ou retornar vazio, retorna {} (UI usa fallback hardcoded).
    """
    df = fetch_sia_by_cid(cid_prefix)
    if df.empty:
        return {}
    total = df["total"].sum()
    if total == 0:
        return {}
    result = {}
    for _, row in df.iterrows():
        pct = round((row["total"] / total) * 100, 1)
        if pct >= 1.0:
            result[row["uf"]] = pct
    return result
