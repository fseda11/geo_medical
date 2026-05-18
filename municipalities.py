"""
municipalities.py — Dados de municípios + filtragem por distância rodoviária
  1. Carrega CSV com todos os municípios brasileiros (lat/lng via IBGE/kelvins)
  2. Pré-filtra por distância em linha reta (barato, local)
  3. Chama Distance Matrix para obter distância rodoviária real
  4. Retorna DataFrame com municípios acessíveis em ≤ max_km por estrada
"""

import math
import time
from typing import Dict, List, Optional

import pandas as pd
import requests
import streamlit as st

from config import (
    MUNICIPALITIES_CSV_URL,
    STRAIGHT_LINE_FACTOR,
    DISTANCE_MATRIX_BATCH,
    GOOGLE_API_KEY,
    GMAPS_DISTANCE_URL,
)

OSRM_TABLE_URL = "http://router.project-osrm.org/table/v1/driving"


# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Carregamento do CSV de municípios ─────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def _load_municipalities_csv() -> pd.DataFrame:
    """
    Carrega o CSV kelvins/municipios-brasileiros.
    Colunas relevantes:
      codigo_ibge, nome, latitude, longitude, uf, estado, capital
    Cache de 24h (dado estático).
    """
    try:
        df = pd.read_csv(MUNICIPALITIES_CSV_URL, dtype={"codigo_ibge": str})
        df.columns = df.columns.str.strip().str.lower()
        # Garante que latitude/longitude são float
        df["latitude"]  = pd.to_numeric(df["latitude"],  errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        df = df.dropna(subset=["latitude", "longitude"])
        return df
    except Exception as e:
        st.error(f"Erro ao carregar base de municípios: {e}")
        return pd.DataFrame()


def get_candidate_municipalities(
    origin_lat: float,
    origin_lng: float,
    max_road_km: float,
) -> pd.DataFrame:
    """
    Filtra municípios com distância em linha reta ≤ max_road_km × STRAIGHT_LINE_FACTOR.
    Serve como pré-filtro para reduzir chamadas à Distance Matrix.
    """
    df = _load_municipalities_csv()
    if df.empty:
        return df

    max_straight = max_road_km * STRAIGHT_LINE_FACTOR

    df["straight_km"] = df.apply(
        lambda r: haversine_km(origin_lat, origin_lng, r["latitude"], r["longitude"]),
        axis=1,
    )

    # Exclui o próprio município de origem (distância ~0)
    candidates = df[(df["straight_km"] > 0.5) & (df["straight_km"] <= max_straight)].copy()
    return candidates.reset_index(drop=True)


# ── OSRM fallback ─────────────────────────────────────────────────────────────

def _fill_batch_osrm(
    origin_lat: float,
    origin_lng: float,
    df: pd.DataFrame,
    batch_start: int,
    batch_df: pd.DataFrame,
) -> None:
    """Preenche road_km/duration_text via OSRM Table Service (público, sem chave)."""
    coords = f"{origin_lng},{origin_lat}"
    for _, row in batch_df.iterrows():
        coords += f";{row['longitude']},{row['latitude']}"

    url = f"{OSRM_TABLE_URL}/{coords}"
    params = {"sources": "0", "annotations": "distance,duration"}
    try:
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        if data.get("code") == "Ok":
            distances = data["distances"][0]   # distâncias em metros desde a origem
            durations  = data["durations"][0]  # segundos
            for j, (dist_m, dur_s) in enumerate(zip(distances[1:], durations[1:])):
                global_idx = batch_start + j
                if dist_m is not None and dist_m > 0:
                    df.at[global_idx, "road_km"] = round(dist_m / 1000, 1)
                    secs = int(dur_s or 0)
                    h, m = divmod(secs // 60, 60)
                    df.at[global_idx, "duration_text"] = f"{h}h {m}min" if h else f"{m}min"
    except Exception as e:
        st.warning(f"⚠️ Erro no OSRM (lote {batch_start}): {e}")


# ── Distance Matrix em lotes ──────────────────────────────────────────────────

def _batch_road_distances(
    origin_lat: float,
    origin_lng: float,
    df: pd.DataFrame,
    api_key: str,
    progress_bar=None,
    progress_text_slot=None,
) -> pd.DataFrame:
    """
    Preenche road_km/duration_text usando Google Routes API.
    Troca automaticamente para OSRM se a Routes API não estiver disponível.
    """
    df = df.copy()
    df["road_km"] = None
    df["duration_text"] = None

    total_batches = math.ceil(len(df) / DISTANCE_MATRIX_BATCH)
    for batch_num, batch_start in enumerate(range(0, len(df), DISTANCE_MATRIX_BATCH)):
        batch_df = df.iloc[batch_start: batch_start + DISTANCE_MATRIX_BATCH]

        if progress_bar is not None:
            progress_bar.progress(batch_num / total_batches)
        if progress_text_slot is not None:
            progress_text_slot.text(f"🛣️ Calculando distâncias… lote {batch_num+1}/{total_batches}")

        # ── Tenta Google Distance Matrix API (clássica) ───────────────────────
        params = {
            "origins":      f"{origin_lat},{origin_lng}",
            "destinations": "|".join(
                f"{row['latitude']},{row['longitude']}"
                for _, row in batch_df.iterrows()
            ),
            "mode":  "driving",
            "units": "metric",
            "key":   api_key,
        }

        google_ok = False
        try:
            resp = requests.get(GMAPS_DISTANCE_URL, params=params, timeout=20)
            data = resp.json()
            status = data.get("status")
            if status == "OK":
                elements = data["rows"][0]["elements"]
                for j, elem in enumerate(elements):
                    global_idx = batch_start + j
                    if elem.get("status") == "OK":
                        df.at[global_idx, "road_km"] = round(
                            elem["distance"]["value"] / 1000, 1
                        )
                        secs = elem["duration"]["value"]
                        h, m = divmod(secs // 60, 60)
                        df.at[global_idx, "duration_text"] = (
                            f"{h}h {m}min" if h else f"{m}min"
                        )
                google_ok = True
            else:
                # Mostra o motivo real da falha (REQUEST_DENIED, INVALID_KEY, etc.)
                st.warning(
                    f"⚠️ Google Distance Matrix lote {batch_num+1}: "
                    f"status={status} | {data.get('error_message','')}"
                )
        except Exception as e:
            st.warning(f"⚠️ Google Distance Matrix lote {batch_num+1} exception: {e}")

        if not google_ok:
            # Fallback apenas para ESTE lote — próximo lote tenta Google novamente
            _fill_batch_osrm(origin_lat, origin_lng, df, batch_start, batch_df)
            time.sleep(0.2)
        else:
            time.sleep(0.05)

    return df


# ── Ponto de entrada principal ────────────────────────────────────────────────

def get_reachable_municipalities(
    origin_lat: float,
    origin_lng: float,
    max_road_km: float,
    api_key: str = GOOGLE_API_KEY,
    progress_bar=None,
    progress_text_slot=None,
) -> pd.DataFrame:
    """
    Retorna DataFrame com municípios alcançáveis em ≤ max_road_km por rodovias.
    Colunas adicionadas: straight_km, road_km, duration_text
    """
    candidates = get_candidate_municipalities(origin_lat, origin_lng, max_road_km)

    if candidates.empty:
        st.warning("⚠️ Nenhum candidato por linha reta. O CSV de municípios pode não ter carregado.")
        return candidates

    if progress_text_slot is not None:
        progress_text_slot.text(f"📍 {len(candidates)} municípios pré-filtrados por linha reta — consultando Distance Matrix…")

    with_distances = _batch_road_distances(
        origin_lat, origin_lng,
        candidates,
        api_key,
        progress_bar=progress_bar,
        progress_text_slot=progress_text_slot,
    )

    # Filtra pelos que têm distância rodoviária ≤ max_road_km
    reachable = with_distances[
        with_distances["road_km"].notna() &
        (with_distances["road_km"] <= max_road_km)
    ].copy()

    reachable = reachable.sort_values("road_km").reset_index(drop=True)
    return reachable


# ── Helpers ───────────────────────────────────────────────────────────────────

def municipality_ibge_to_cnes_code(ibge_code: str) -> str:
    """
    Converte código IBGE de 7 dígitos para código CNES de 6 dígitos.
    CNES usa os primeiros 6 dígitos do código IBGE completo.
    """
    return str(ibge_code).strip()[:6]