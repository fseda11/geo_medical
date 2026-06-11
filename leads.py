"""
leads.py — Busca de Leads Complementares via Google Places
  - Associações de Pacientes / ONGs
  - Advogados Especializados em Saúde
"""

import time
import requests
import streamlit as st
from typing import List, Dict
from config import GOOGLE_API_KEY, GMAPS_PLACES_DETAIL_URL

PLACE_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"

_leads_cache: Dict[str, list] = {}


def _get_place_details(place_id: str) -> dict:
    try:
        r = requests.get(
            GMAPS_PLACES_DETAIL_URL,
            params={
                "place_id": place_id,
                "fields": "formatted_phone_number,website,opening_hours,rating,user_ratings_total",
                "key": GOOGLE_API_KEY,
            },
            timeout=5,
        )
        return r.json().get("result", {})
    except Exception:
        return {}


def _search_places(query: str, lat: float, lng: float, radius_m: int = 50_000) -> list:
    cache_key = f"{query}|{round(lat,2)}|{round(lng,2)}|{radius_m}"
    if cache_key in _leads_cache:
        return _leads_cache[cache_key]
    results = []
    next_token = None
    for _ in range(2):  # max 2 páginas = 40 resultados
        params = {"language": "pt-BR", "key": GOOGLE_API_KEY}
        if next_token:
            params["pagetoken"] = next_token
            time.sleep(2)
        else:
            params.update({
                "query":    query,
                "location": f"{lat},{lng}",
                "radius":   radius_m,
                "region":   "br",
            })
        try:
            r = requests.get(PLACE_SEARCH_URL, params=params, timeout=10)
            data = r.json()
            results.extend(data.get("results", []))
            next_token = data.get("next_page_token")
            if not next_token:
                break
        except Exception:
            break
    _leads_cache[cache_key] = results
    return results


@st.cache_data(ttl=3600, show_spinner=False)
def buscar_associacoes(lat: float, lng: float, radius_km: int, doenca: str = "") -> List[Dict]:
    queries = [
        f"associação de pacientes {doenca}",
        f"ong saúde {doenca}",
        "associação de pacientes doenças raras",
        "instituto saúde pacientes",
    ]
    EXCLUIR_PALAVRAS = [
        "floresta","amazônia","amazonia","ambiental","meio ambiente","animal",
        "fauna","flora","esporte","futebol","tênis","atletismo","esportivo",
        "cultural","música","musica","arte","teatro","dança","danca","cinema",
        "sindical","sindicato","trabalhador","funcionário","moradores",
        "condomínio","condominios","religiosa","igreja","paróquia","espirita",
        "estudantil","universitária","universitaria","agropecuária","agricola",
    ]
    seen, out = set(), []
    for q in queries:
        for place in _search_places(q, lat, lng, radius_km * 1000):
            pid = place.get("place_id", "")
            if pid in seen:
                continue
            # Exclui associações claramente fora do contexto de saúde/medicamentos
            nome_lower = place.get("name", "").lower()
            if any(w in nome_lower for w in EXCLUIR_PALAVRAS):
                continue
            seen.add(pid)
            loc = place.get("geometry", {}).get("location", {})
            details = _get_place_details(pid) if pid else {}
            out.append({
                "nome":       place.get("name", ""),
                "endereco":   place.get("formatted_address", ""),
                "latitude":   loc.get("lat"),
                "longitude":  loc.get("lng"),
                "telefone":   details.get("formatted_phone_number", ""),
                "website":    details.get("website", ""),
                "avaliacao":  place.get("rating", ""),
                "tipo":       "Associação / ONG",
                "place_id":   pid,
            })
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def buscar_advogados(lat: float, lng: float, radius_km: int, especialidade: str = "saúde") -> List[Dict]:
    queries = [
        f"advogado direito à saúde {especialidade}",
        f"escritório advocacia saúde medicamentos",
        f"advogado medicamentos alto custo",
        f"advogado doenças raras judicialização",
    ]
    seen, out = set(), []
    for q in queries:
        for place in _search_places(q, lat, lng, radius_km * 1000):
            pid = place.get("place_id", "")
            if pid in seen:
                continue
            seen.add(pid)
            loc = place.get("geometry", {}).get("location", {})
            details = _get_place_details(pid) if pid else {}
            out.append({
                "nome":       place.get("name", ""),
                "endereco":   place.get("formatted_address", ""),
                "latitude":   loc.get("lat"),
                "longitude":  loc.get("lng"),
                "telefone":   details.get("formatted_phone_number", ""),
                "website":    details.get("website", ""),
                "avaliacao":  place.get("rating", ""),
                "qtd_avaliacoes": place.get("user_ratings_total", 0),
                "tipo":       "Advogado / Escritório",
                "place_id":   pid,
            })
    return out


def build_leads_map(leads: List[Dict], origin_lat: float, origin_lng: float) -> object:
    import folium
    m = folium.Map(location=[origin_lat, origin_lng], zoom_start=10, tiles=None)
    folium.TileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satélite",
    ).add_to(m)
    folium.TileLayer(
        "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="CartoDB", name="Mapa", max_zoom=19,
    ).add_to(m)

    layer_assoc = folium.FeatureGroup(name="🤝 Associações / ONGs")
    layer_adv   = folium.FeatureGroup(name="⚖️ Advogados")

    for lead in leads:
        lat = lead.get("latitude")
        lng = lead.get("longitude")
        if not lat or not lng:
            continue
        try:
            lat, lng = float(lat), float(lng)
        except Exception:
            continue
        tipo  = lead.get("tipo", "")
        color = "#6A1B9A" if "Assoc" in tipo else "#1565C0"
        icon  = "🤝" if "Assoc" in tipo else "⚖️"
        tip   = (
            f"<div style='font-family:Arial;font-size:13px'>"
            f"<b>{lead['nome']}</b><br>"
            f"<span style='color:#888'>{tipo}</span><br>"
            f"📍 {lead.get('endereco','—')}<br>"
            f"📞 {lead.get('telefone','—')}<br>"
            + (f"⭐ {lead.get('avaliacao','')} ({lead.get('qtd_avaliacoes',0)} aval.)" if lead.get("avaliacao") else "")
            + "</div>"
        )
        marker = folium.Marker(
            location=[lat, lng],
            tooltip=folium.Tooltip(tip, sticky=True),
            popup=folium.Popup(tip, max_width=280),
            icon=folium.DivIcon(
                html=f'<div style="background:{color};color:white;border-radius:50%;'
                     f'width:30px;height:30px;display:flex;align-items:center;'
                     f'justify-content:center;font-size:14px;border:2px solid rgba(0,0,0,.2)">{icon}</div>',
                icon_size=(30,30), icon_anchor=(15,15),
            ),
        )
        if "Assoc" in tipo:
            marker.add_to(layer_assoc)
        else:
            marker.add_to(layer_adv)

    layer_assoc.add_to(m)
    layer_adv.add_to(m)
    folium.Marker(
        location=[origin_lat, origin_lng],
        tooltip="📍 Origem",
        icon=folium.Icon(color="darkred", icon="home", prefix="fa"),
    ).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    return m
