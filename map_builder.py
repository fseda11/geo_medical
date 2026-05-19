"""
map_builder.py — Constrói o mapa Folium com todas as camadas:
  - Origem
  - Polilinha de rotas principais (Google Directions)
  - Municípios alcançáveis (círculos coloridos por distância)
  - Cluster de estabelecimentos de saúde (ícones por categoria)
"""

from typing import Dict, Optional, Tuple

import folium
import pandas as pd
from folium.plugins import MarkerCluster, MiniMap

from config import CATEGORY_COLORS, CATEGORY_ICONS, GOOGLE_API_KEY
from geocoding import get_route_polyline


# ── Helpers de cor ────────────────────────────────────────────────────────────

def _distance_color(road_km: float, max_km: float) -> str:
    """Gradiente verde→amarelo→vermelho conforme distância."""
    ratio = road_km / max_km
    if ratio < 0.33:
        return "#2196F3"   # azul — perto
    elif ratio < 0.66:
        return "#FF9800"   # laranja — médio
    return "#9E9E9E"       # cinza — longe


def _popup_html(est: dict) -> str:
    icon = CATEGORY_ICONS.get(est.get("category", "outro"), "🏢")
    score = est.get("score_potencial", 0)
    score_color = "#4CAF50" if score >= 60 else "#FF9800" if score >= 35 else "#9E9E9E"

    leitos     = est.get("qt_leito_internacao") or "—"
    leitos_sus = est.get("qt_leito_sus") or "—"
    telefone   = est.get("nu_telefone") or "—"
    gestao     = est.get("tp_pfpj") or "—"
    duration   = est.get("duration_text") or "—"

    return f"""
    <div style="font-family:Arial,sans-serif;min-width:240px;max-width:280px">
      <div style="background:#263238;color:#fff;padding:8px 12px;border-radius:6px 6px 0 0;
                  font-size:13px;font-weight:bold">
        {icon} {est.get("no_razao_social") or "Estabelecimento"}
      </div>
      <div style="padding:10px 12px;border:1px solid #eee;border-radius:0 0 6px 6px">
        <p style="margin:0 0 4px;color:#666;font-size:11px">
          {est.get("ds_tipo_unidade","—")}
        </p>
        <hr style="margin:6px 0;border-color:#eee">
        <table style="width:100%;font-size:12px;border-collapse:collapse">
          <tr><td style="color:#888;padding:2px 0">CNES</td>
              <td style="font-weight:bold">{est.get("co_cnes","—")}</td></tr>
          <tr><td style="color:#888;padding:2px 0">Município</td>
              <td>{est.get("municipio_nome","—")} / {est.get("uf","—")}</td></tr>
          <tr><td style="color:#888;padding:2px 0">Distância</td>
              <td>{est.get("road_km","—")} km ({duration})</td></tr>
          <tr><td style="color:#888;padding:2px 0">Leitos</td>
              <td>{leitos} total / {leitos_sus} SUS</td></tr>
          <tr><td style="color:#888;padding:2px 0">Gestão</td>
              <td>{gestao}</td></tr>
          <tr><td style="color:#888;padding:2px 0">Telefone</td>
              <td>{telefone}</td></tr>
        </table>
        <div style="margin-top:8px;text-align:right">
          <span style="background:{score_color};color:#fff;padding:3px 8px;
                       border-radius:12px;font-size:11px;font-weight:bold">
            Potencial: {score}/100
          </span>
        </div>
      </div>
    </div>
    """


# ── Construção principal do mapa ──────────────────────────────────────────────

def build_map(
    origin: Dict,
    municipalities: pd.DataFrame,
    establishments: pd.DataFrame,
    max_km: float = 150,
    draw_routes_to: int = 8,  # nº de municípios top para desenhar rota real
) -> folium.Map:
    """
    Retorna mapa Folium completo com todas as camadas.

    origin: {'lat': float, 'lng': float, 'formatted_address': str}
    """

    # ── Mapa base ──────────────────────────────────────────────────────────────
    m = folium.Map(
        location=[origin["lat"], origin["lng"]],
        zoom_start=9,
        tiles=None,
    )

    # Tiles: Streets + Satellite toggle
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="© OpenStreetMap / CartoDB",
        name="Mapa (padrão)",
        max_zoom=19,
    ).add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satélite",
        max_zoom=19,
    ).add_to(m)

    # ── Círculo de referência (linha reta, apenas visual) ─────────────────────
    folium.Circle(
        location=[origin["lat"], origin["lng"]],
        radius=max_km * 1000,
        color="#B0BEC5",
        weight=1,
        fill=False,
        dash_array="6 4",
        tooltip=f"Raio de {max_km} km (referência — área real é por rodovias)",
    ).add_to(m)

    # ── Rotas reais (Directions API) para os N municípios mais próximos ───────
    route_layer = folium.FeatureGroup(name="🛣️ Rotas principais", show=True)

    if not municipalities.empty:
        top_munis = municipalities.head(draw_routes_to)
        for _, muni in top_munis.iterrows():
            coords = get_route_polyline(
                origin["lat"], origin["lng"],
                muni["latitude"], muni["longitude"],
                api_key=GOOGLE_API_KEY,
            )
            if coords:
                folium.PolyLine(
                    coords,
                    weight=3,
                    color="#1565C0",
                    opacity=0.55,
                    tooltip=None,
                ).add_to(route_layer)

    route_layer.add_to(m)

    # ── Estabelecimentos de saúde (clusters por categoria) ────────────────────
    if not establishments.empty:
        # Clusters separados por categoria para controle de camadas
        categories = establishments["category"].unique()

        for cat in categories:
            cat_df = establishments[establishments["category"] == cat]
            color_hex = CATEGORY_COLORS.get(cat, "#757575")
            icon_emoji = CATEGORY_ICONS.get(cat, "🏢")

            # Nome amigável para o controle de camadas
            cat_label = {
                "hospital": "🏥 Hospitais",
                "upa": "🚨 UPAs / Pronto-Socorros",
                "clinica": "🏨 Clínicas / Especialidades",
                "farmacia": "💊 Farmácias",
                "ubs": "🩺 UBS / Postos",
                "outro": "🏢 Outros",
            }.get(cat, cat.capitalize())

            cluster = MarkerCluster(
                name=cat_label,
                show=True,
                icon_create_function="""function(cluster) {
    var children = cluster.getAllChildMarkers();
    var total = 0, count = 0;
    children.forEach(function(m) {
        var html = m.options.icon && m.options.icon.options ? m.options.icon.options.html : '';
        var match = html ? html.match(/data-score="([0-9]+)"/) : null;
        if (match) { total += parseInt(match[1]); count++; }
    });
    var avg = count > 0 ? total / count : 0;
    var bg, bd;
    if      (avg >= 60) { bg='#1B5E20'; bd='#2E7D32'; }
    else if (avg >= 40) { bg='#E65100'; bd='#F57C00'; }
    else if (avg >= 25) { bg='#F57F17'; bd='#F9A825'; }
    else                { bg='#37474F'; bd='#546E7A'; }
    return L.divIcon({
        html: '<div style="background:'+bg+';border:3px solid '+bd+
              ';color:#fff;border-radius:50%;width:36px;height:36px;'+
              'display:flex;align-items:center;justify-content:center;'+
              'font-weight:bold;font-size:13px;">'+cluster.getChildCount()+'</div>',
        className:'', iconSize: L.point(36,36)
    });
}""",
                options={
                    "spiderfyOnMaxZoom": True,
                    "showCoverageOnHover": False,
                    "maxClusterRadius": 60,
                    "disableClusteringAtZoom": 14,
                },
            )

            for _, est in cat_df.iterrows():
                lat = est.get("latitude")
                lng = est.get("longitude")
                if lat is None or lng is None:
                    continue
                try:
                    lat, lng = float(lat), float(lng)
                except Exception:
                    continue

                score = est.get("score_potencial", 0)
                # Cor do marker individual baseada no score
                if score >= 60:   mc = "#1B5E20"  # verde escuro
                elif score >= 40: mc = "#E65100"  # laranja
                elif score >= 25: mc = "#F9A825"  # amarelo
                else:             mc = "#546E7A"  # cinza

                # Nome preferencial: fantasia > razao_social
                nome = (est.get("no_fantasia") or est.get("no_razao_social") or "Estabelecimento").strip()
                tipo  = est.get("ds_tipo_unidade", "—")
                muni  = est.get("municipio_nome", "—")
                uf    = est.get("uf", "")
                dist  = est.get("road_km", "—")
                tel_c = est.get("nu_telefone_cnes") or "—"
                tel_g = est.get("nu_telefone_google") or "—"

                # divIcon com data-score para icon_create_function calcular média
                div_html = (
                    f'<div data-score="{score}" style="'
                    f'background:{mc};border:2px solid rgba(0,0,0,.25);'
                    f'color:#fff;border-radius:50%;width:26px;height:26px;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'font-size:12px;">{CATEGORY_ICONS.get(cat,"🏢")}</div>'
                )

                _loc = f"{muni} / {uf}" if uf else muni
                tooltip_html = (
                    f"<div style='font-family:Arial;font-size:13px;min-width:220px'>"
                    f"<b>{nome}</b><br>"
                    f"<span style='color:#888'>{tipo}</span><br>"
                    f"📍 {_loc} · {dist} km<br>"
                    f"📞 {tel_c}"
                    f"{' &nbsp;|&nbsp; 🔍 '+tel_g if tel_g != '—' else ''}<br>"
                    f"<b style='color:{mc}'>⭐ Score {score}/100</b>"
                    f"</div>"
                )

                folium.Marker(
                    location=[lat, lng],
                    popup=folium.Popup(_popup_html(est), max_width=300),
                    tooltip=folium.Tooltip(tooltip_html, sticky=True),
                    icon=folium.DivIcon(html=div_html, icon_size=(26,26), icon_anchor=(13,13)),
                ).add_to(cluster)

            cluster.add_to(m)

    # ── Marcador de origem ────────────────────────────────────────────────────
    folium.Marker(
        location=[origin["lat"], origin["lng"]],
        popup=folium.Popup(
            f"<b>🏠 ORIGEM</b><br>{origin.get('formatted_address','')}",
            max_width=250,
        ),
        tooltip="📍 Cidade de origem",
        icon=folium.Icon(color="darkred", icon="home", prefix="fa"),
        z_index_offset=1000,
    ).add_to(m)

    # ── Legenda HTML ──────────────────────────────────────────────────────────
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:12px 16px;border-radius:8px;
                box-shadow:0 2px 10px rgba(0,0,0,.2);font-family:Arial;font-size:12px;color:#222 !important;
                color:#222 !important;">
      <b style="display:block;margin-bottom:8px;color:#222">Potencial de Alto Custo</b>
      <div><span style="background:#4CAF50;color:#fff;padding:2px 8px;
                        border-radius:10px;margin-right:6px">≥ 60</span> Alto</div>
      <div style="margin-top:4px">
        <span style="background:#FF9800;color:#fff;padding:2px 8px;
                     border-radius:10px;margin-right:6px">35–59</span> Médio
      </div>
      <div style="margin-top:4px">
        <span style="background:#9E9E9E;color:#fff;padding:2px 8px;
                     border-radius:10px;margin-right:6px">&lt; 35</span> Baixo
      </div>
      <hr style="margin:8px 0;border-color:#eee">
      <b style="display:block;margin-bottom:6px;color:#222">Distância rodoviária</b>
      <div style="color:#222"><span style="color:#2196F3">●</span> Até 33%</div>
      <div style="color:#222"><span style="color:#FF9800">●</span> 33–66%</div>
      <div style="color:#222"><span style="color:#9E9E9E">●</span> 66–100%</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # ── Mini-mapa e controle de camadas ───────────────────────────────────────
    MiniMap(toggle_display=True, tile_layer="CartoDB positron").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    return m