"""
geocoding.py — Wrappers para Google Maps Platform
  - Autocomplete de cidades (Places API)
  - Geocodificação por place_id ou texto
  - Distance Matrix (distâncias rodoviárias em lote)
  - Directions (polyline de rota para visualização)
"""

import time
import polyline as polyline_lib
import requests
import streamlit as st
from typing import Dict, List, Optional, Tuple

from config import (
    GOOGLE_API_KEY,
    GMAPS_GEOCODE_URL,
    GMAPS_DISTANCE_URL,
    GMAPS_DIRECTIONS_URL,
    GMAPS_PLACES_AC_URL,
    GMAPS_PLACES_DETAIL_URL,
    DISTANCE_MATRIX_BATCH,
)


# ── Autocomplete ──────────────────────────────────────────────────────────────

def search_cities_autocomplete(query: str) -> List[Tuple[str, str]]:
    """
    Retorna lista de (descrição, place_id) para uso no searchbox.
    Restringe busca a cidades brasileiras.
    """
    if len(query) < 2:
        return []
    params = {
        "input": query,
        "types": "(cities)",
        "language": "pt-BR",
        "components": "country:br",
        "key": GOOGLE_API_KEY,
    }
    try:
        resp = requests.get(GMAPS_PLACES_AC_URL, params=params, timeout=8)
        data = resp.json()
        return [
            (p["description"], p["place_id"])
            for p in data.get("predictions", [])
        ]
    except Exception:
        return []


# ── Autocomplete de bairros ───────────────────────────────────────────────────

def search_neighborhoods_autocomplete(query: str, city_lat: float = 0, city_lng: float = 0) -> list:
    """Autocomplete de bairros dentro de uma cidade brasileira."""
    if len(query) < 2:
        return []
    params = {
        "input":      query,
        "types":      "(regions)",
        "language":   "pt-BR",
        "components": "country:br",
        "key":        GOOGLE_API_KEY,
    }
    if city_lat and city_lng:
        params["location"]     = f"{city_lat},{city_lng}"
        params["radius"]       = "30000"
        params["strictbounds"] = "true"
    try:
        resp = requests.get(GMAPS_PLACES_AC_URL, params=params, timeout=8)
        return [
            (p["description"], p["place_id"])
            for p in resp.json().get("predictions", [])
        ]
    except Exception:
        return []


# ── Geocodificação ────────────────────────────────────────────────────────────

def geocode_by_place_id(place_id: str) -> Optional[Dict]:
    """Retorna {lat, lng, formatted_address} a partir de um place_id."""
    params = {
        "place_id": place_id,
        "fields": "geometry,formatted_address",
        "key": GOOGLE_API_KEY,
    }
    try:
        resp = requests.get(GMAPS_PLACES_DETAIL_URL, params=params, timeout=8)
        data = resp.json()
        result = data.get("result", {})
        loc = result.get("geometry", {}).get("location", {})
        if loc:
            return {
                "lat": loc["lat"],
                "lng": loc["lng"],
                "formatted_address": result.get("formatted_address", ""),
            }
    except Exception:
        pass
    return None


def geocode_by_text(text: str) -> Optional[Dict]:
    """Geocodifica texto livre. Fallback quando não há place_id."""
    params = {
        "address": text,
        "region": "br",
        "language": "pt-BR",
        "key": GOOGLE_API_KEY,
    }
    try:
        resp = requests.get(GMAPS_GEOCODE_URL, params=params, timeout=8)
        data = resp.json()
        if data["status"] == "OK":
            loc = data["results"][0]["geometry"]["location"]
            return {
                "lat": loc["lat"],
                "lng": loc["lng"],
                "formatted_address": data["results"][0]["formatted_address"],
            }
    except Exception:
        pass
    return None


# ── Distance Matrix ───────────────────────────────────────────────────────────

def get_road_distances(
    origin_lat: float,
    origin_lng: float,
    destinations: List[Dict],  # cada dict precisa de 'latitude' e 'longitude'
    api_key: str = GOOGLE_API_KEY,
) -> List[Optional[float]]:
    """
    Calcula distâncias rodoviárias (km) do ponto de origem até cada destino.
    Processa em lotes de DISTANCE_MATRIX_BATCH para respeitar limites da API.
    Retorna lista de floats (km) ou None se o destino for inacessível.
    """
    results = [None] * len(destinations)

    for batch_start in range(0, len(destinations), DISTANCE_MATRIX_BATCH):
        batch = destinations[batch_start: batch_start + DISTANCE_MATRIX_BATCH]

        body = {
            "origins": [{
                "waypoint": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lng}}}
            }],
            "destinations": [
                {"waypoint": {"location": {"latLng": {"latitude": d["latitude"], "longitude": d["longitude"]}}}}
                for d in batch
            ],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_UNAWARE",
        }
        headers = {
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,status",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(GMAPS_DISTANCE_URL, json=body, headers=headers, timeout=20)
            elements = resp.json()

            if isinstance(elements, list):
                for elem in elements:
                    status_code = (elem.get("status") or {}).get("code")
                    if status_code:
                        continue
                    dest_idx = elem.get("destinationIndex", 0)
                    global_idx = batch_start + dest_idx
                    dist_m = elem.get("distanceMeters")
                    if dist_m is not None:
                        results[global_idx] = round(dist_m / 1000, 2)
        except Exception as e:
            st.warning(f"⚠️ Erro na Routes API (lote {batch_start}): {e}")

        time.sleep(0.05)

    return results


# ── Directions (polylines para visualização) ──────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_route_polyline(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    api_key: str = GOOGLE_API_KEY,
) -> Optional[List[Tuple[float, float]]]:
    """
    Retorna lista de coordenadas (lat, lng) decodificadas da polyline da rota.
    Cache de 1 hora para evitar chamadas repetidas.
    """
    params = {
        "origin": f"{origin_lat},{origin_lng}",
        "destination": f"{dest_lat},{dest_lng}",
        "mode": "driving",
        "language": "pt-BR",
        "key": api_key,
    }
    try:
        resp = requests.get(GMAPS_DIRECTIONS_URL, params=params, timeout=15)
        data = resp.json()
        if data.get("status") == "OK" and data.get("routes"):
            encoded = data["routes"][0]["overview_polyline"]["points"]
            return polyline_lib.decode(encoded)
    except Exception:
        pass
    return None
