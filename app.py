"""
app.py — Health Route Intelligence
Mapeamento de estabelecimentos de saúde por distância rodoviária.

Run:
    streamlit run app.py
"""

import io

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox

from config import DEFAULT_DISTANCE_KM, GOOGLE_API_KEY, CATEGORY_ICONS
from cnes import get_establishments_for_municipalities, summarize_establishments
from geocoding import geocode_by_place_id, geocode_by_text, search_cities_autocomplete
from map_builder import build_map
from municipalities import get_reachable_municipalities

# ── Página ────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Health Route Intelligence",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS customizado ───────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
      [data-testid="stSidebar"] { background: #1C2833; }
      [data-testid="stSidebar"] * { color: #ECF0F1 !important; }
      [data-testid="stMetricValue"] { font-size: 2rem; }
      .block-container { padding-top: 1rem; }
      div[data-testid="stDataFrame"] { border: 1px solid #eee; border-radius: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style="background:linear-gradient(135deg,#1565C0,#0D47A1);
                padding:20px 28px;border-radius:10px;margin-bottom:20px">
      <h1 style="color:#fff;margin:0;font-size:1.8rem">
        🏥 Health Route Intelligence
      </h1>
      <p style="color:#90CAF9;margin:4px 0 0">
        Mapeamento de estabelecimentos de saúde por rotas rodoviárias · Dados CNES/DATASUS
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔍 Configurar busca")

    # Autocomplete Google Places
    selected_city = st_searchbox(
        search_function=search_cities_autocomplete,
        placeholder="Digite o nome da cidade…",
        label="Cidade de origem",
        key="city_searchbox",
    )

    distance_km = st.slider(
        "Distância máxima por rodovias (km)",
        min_value=50,
        max_value=300,
        value=DEFAULT_DISTANCE_KM,
        step=25,
        help="Distância rodoviária real, calculada via Google Distance Matrix.",
    )

    st.markdown("---")
    st.markdown("### 🔧 Filtros de estabelecimento")

    filter_categories = st.multiselect(
        "Categorias",
        options=["hospital", "upa", "clinica", "farmacia", "ubs", "outro"],
        default=["hospital", "upa", "clinica", "farmacia", "ubs", "outro"],
        format_func=lambda c: f"{CATEGORY_ICONS.get(c, '🏢')} {c.capitalize()}",
    )

    only_relevant = st.checkbox(
        "Apenas relevantes para alto custo",
        value=False,
        help="Filtra hospitais, clínicas de especialidade, UPAs, farmácias, hospital dia.",
    )

    min_score = st.slider("Score mínimo de potencial", 0, 80, 0, 5)

    st.markdown("---")
    st.markdown("### 🗺️ Visualização")

    draw_routes = st.number_input(
        "Rotas no mapa (Google Directions)",
        min_value=0, max_value=500, value=10, step=5,
        help="0 = desativa. Cada rota usa 1 chamada Directions API (cacheada 1h).",
    )

    st.markdown("---")

    search_btn = st.button(
        "🚀 Buscar estabelecimentos",
        type="primary",
        use_container_width=True,
    )

# ── Estado da sessão ──────────────────────────────────────────────────────────
if "result_map"  not in st.session_state: st.session_state.result_map  = None
if "result_df"   not in st.session_state: st.session_state.result_df   = None
if "result_munis" not in st.session_state: st.session_state.result_munis = None
if "origin_data" not in st.session_state: st.session_state.origin_data = None

# ── Lógica principal ──────────────────────────────────────────────────────────
if search_btn:

    # Resolve cidade selecionada
    city_query = None
    if selected_city:
        # st_searchbox pode retornar (description, place_id) ou só string
        if isinstance(selected_city, tuple):
            city_query = selected_city  # (description, place_id)
        else:
            city_query = str(selected_city)

    if not city_query:
        st.sidebar.error("Selecione uma cidade para buscar.")
        st.stop()

    # ── Geocodificação ────────────────────────────────────────────────────────
    with st.status("📍 Geocodificando cidade de origem…", expanded=True) as status:
        if isinstance(city_query, tuple):
            origin = geocode_by_place_id(city_query[1])
            if not origin:
                origin = geocode_by_text(city_query[0])
        else:
            # st_searchbox retorna o place_id (2º elemento do tuple) como string
            origin = geocode_by_place_id(city_query)
            if not origin:
                origin = geocode_by_text(city_query)

        if not origin:
            st.error("❌ Cidade não encontrada. Verifique o nome e tente novamente.")
            st.stop()

        st.write(f"✅ **{origin['formatted_address']}** ({origin['lat']:.4f}, {origin['lng']:.4f})")
        status.update(label="Cidade geocodificada ✅")

    # ── Municípios acessíveis ─────────────────────────────────────────────────
    with st.status("🛣️ Calculando distâncias rodoviárias…", expanded=True) as status:
        prog_bar  = st.progress(0)
        prog_text = st.empty()

        municipalities = get_reachable_municipalities(
            origin_lat=origin["lat"],
            origin_lng=origin["lng"],
            max_road_km=distance_km,
            api_key=GOOGLE_API_KEY,
            progress_bar=prog_bar,
            progress_text_slot=prog_text,
        )

        prog_bar.progress(1.0)
        prog_text.empty()

        n_munis = len(municipalities)
        st.write(f"✅ **{n_munis} municípios** dentro de {distance_km} km por rodovias.")
        status.update(label=f"{n_munis} municípios encontrados ✅")

    if municipalities.empty:
        st.error(
            "Nenhum município encontrado. Possíveis causas:\n"
            "- A Distance Matrix API retornou erro (verifique avisos acima)\n"
            "- A chave de API não tem Distance Matrix habilitada no Google Cloud Console\n"
            "- O CSV de municípios não carregou (verifique conexão com GitHub)"
        )
        st.stop()

    # ── Consulta CNES ─────────────────────────────────────────────────────────
    with st.status("🏥 Consultando CNES/DATASUS…", expanded=True) as status:
        prog_bar2  = st.progress(0)
        prog_text2 = st.empty()

        establishments = get_establishments_for_municipalities(
            municipalities=municipalities,
            only_relevant=only_relevant,
            progress_bar=prog_bar2,
            progress_text_slot=prog_text2,
        )

        prog_bar2.progress(1.0)
        prog_text2.empty()

        n_est = len(establishments)
        st.write(f"✅ **{n_est} estabelecimentos** encontrados.")
        status.update(label=f"{n_est} estabelecimentos carregados ✅")

    # Aplica filtros pós-busca
    if not establishments.empty:
        if filter_categories:
            establishments = establishments[
                establishments["category"].isin(filter_categories)
            ]
        if min_score > 0:
            establishments = establishments[
                establishments["score_potencial"] >= min_score
            ]

    # ── Monta mapa ────────────────────────────────────────────────────────────
    with st.spinner("🗺️ Construindo mapa…"):
        fmap = build_map(
            origin=origin,
            municipalities=municipalities,
            establishments=establishments,
            max_km=distance_km,
            draw_routes_to=draw_routes,
        )

    # Salva na sessão
    st.session_state.result_map   = fmap
    st.session_state.result_df    = establishments
    st.session_state.result_munis = municipalities
    st.session_state.origin_data  = origin

# ── Exibição dos resultados ───────────────────────────────────────────────────
if st.session_state.result_map is not None:
    origin        = st.session_state.origin_data
    municipalities = st.session_state.result_munis
    establishments = st.session_state.result_df
    summary        = summarize_establishments(establishments)

    # ── Métricas principais ───────────────────────────────────────────────────
    cols = st.columns(7)
    metrics = [
        ("🏙️ Municípios",    summary.get("municipios",     0)),
        ("🏥 Hospitais",      summary.get("hospitais",      0)),
        ("🏨 Clínicas",       summary.get("clinicas",       0)),
        ("🚨 UPAs",           summary.get("upas",           0)),
        ("💊 Farmácias",      summary.get("farmacias",      0)),
        ("🩺 UBS / Postos",   summary.get("ubs",            0)),
        ("⭐ Alto potencial", summary.get("alto_potencial", 0)),
    ]
    for col, (label, val) in zip(cols, metrics):
        col.metric(label, f"{val:,}")

    st.markdown("---")

    # ── Mapa full-width ────────────────────────────────────────────────────────
    st.markdown("#### 🗺️ Mapa de cobertura")
    st.caption("💡 Use o controle de camadas ▶ (canto superior direito) para adicionar hospitais, clínicas, farmácias etc.")
    st_folium(
        st.session_state.result_map,
        use_container_width=True,
        height=640,
        returned_objects=[],
    )

    st.markdown("---")

    # ── Tabela única: Score + CNES + Export ────────────────────────────────────
    if not establishments.empty:
        RENAME_FULL = {
            "score_potencial":   "⭐ Score",
            "co_cnes":           "Cód. CNES",
            "co_cnpj":           "CNPJ",
            "no_razao_social":   "Razão Social",
            "no_fantasia":       "Nome Fantasia",
            "ds_tipo_unidade":   "Tipo",
            "no_logradouro":     "Endereço",
            "nu_endereco":       "Número",
            "no_bairro":         "Bairro",
            "co_cep":            "CEP",
            "municipio_nome":    "Município",
            "uf":                "UF",
            "road_km":           "Dist. (km)",
            "duration_text":     "Tempo",
            "nu_telefone":       "Telefone",
            "no_email":          "E-mail",
            "tp_pfpj":           "Natureza",
            "tp_gestao":         "Gestão",
            "turno_atendimento": "Turno",
            "atend_sus":         "Atend. SUS",
            "tem_cirurgia":      "Ctr. Cirúrgico",
            "tem_obstetrico":    "Ctr. Obstétrico",
            "dt_atualizacao":    "Atualização",
        }
        _drop = ["latitude", "longitude", "category", "tp_unidade",
                 "qt_leito_internacao", "qt_leito_sus", "atend_ambulatorial"]

        # Controles: busca + export na mesma linha
        col_search, col_xlsx, col_csv = st.columns([3, 1, 1])
        with col_search:
            search_term = st.text_input("🔎 Filtrar por nome…", key="tab_search")

        df_filtered = establishments
        if search_term:
            df_filtered = df_filtered[
                df_filtered["no_razao_social"].str.contains(search_term, case=False, na=False)
            ]

        df_disp = df_filtered.drop(columns=_drop, errors="ignore")
        df_disp = df_disp.rename(columns={k: v for k, v in RENAME_FULL.items() if k in df_disp.columns})

        city_slug = (
            origin.get("formatted_address", "busca")
            .split(",")[0].strip().replace(" ", "_").lower()
        )

        with col_xlsx:
            try:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    df_filtered.drop(columns=_drop, errors="ignore").to_excel(
                        writer, sheet_name="Estabelecimentos", index=False)
                    if municipalities is not None:
                        municipalities.to_excel(writer, sheet_name="Municípios", index=False)
                buf.seek(0)
                st.download_button("⬇️ Excel", data=buf,
                    file_name=f"health_route_{city_slug}_{distance_km}km.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)
            except Exception:
                st.warning("openpyxl não disponível")

        with col_csv:
            st.download_button("⬇️ CSV",
                data=df_filtered.drop(columns=_drop, errors="ignore").to_csv(index=False).encode("utf-8"),
                file_name=f"health_route_{city_slug}_{distance_km}km.csv",
                mime="text/csv", use_container_width=True)

        st.dataframe(
            df_disp,
            use_container_width=True,
            height=500,
            column_config={
                "⭐ Score": st.column_config.ProgressColumn(
                    "⭐ Score", min_value=0, max_value=100, format="%d"),
                "Dist. (km)": st.column_config.NumberColumn(format="%.0f km"),
            },
        )
        st.caption(f"{len(df_filtered):,} estabelecimento(s) exibido(s) de {len(establishments):,} total.")

    else:
        st.info("Nenhum estabelecimento após aplicar os filtros.")

    # ── Aba municípios ─────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("🏙️ Municípios na rota", expanded=False):
        if municipalities is not None and not municipalities.empty:
            _col_map = {
                "nome": "Município", "uf": "UF", "estado": "Estado",
                "road_km": "Dist. (km)", "duration_text": "Tempo estimado",
                "straight_km": "Dist. linear (km)",
            }
            _existing = [c for c in _col_map if c in municipalities.columns]
            st.dataframe(municipalities[_existing].rename(columns=_col_map),
                         use_container_width=True, height=350)
            st.caption(f"{len(municipalities)} município(s) em até {distance_km} km por rodovias.")

            st.markdown(
                f"""
                **Resumo do arquivo:**
                - {summary.get('total', 0):,} estabelecimentos
                - {summary.get('municipios', 0)} municípios
                - Origem: {origin.get('formatted_address', '')}
                - Raio rodoviário: {distance_km} km
                """
            )

else:
    # Estado inicial — tela de boas-vindas
    st.info(
        "👈 **Configure a busca** na barra lateral e clique em **Buscar estabelecimentos** para começar.",
        icon="🗺️",
    )

    st.markdown(
        """
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:20px">
          <div style="background:#E3F2FD;padding:20px;border-radius:10px;border-left:4px solid #1565C0">
            <h4 style="margin:0;color:#1565C0">🛣️ Rotas reais</h4>
            <p style="margin:8px 0 0;color:#555;font-size:14px">
              Distâncias calculadas por Google Distance Matrix — rodovias reais, não raio simples.
            </p>
          </div>
          <div style="background:#E8F5E9;padding:20px;border-radius:10px;border-left:4px solid #2E7D32">
            <h4 style="margin:0;color:#2E7D32">🏥 Dados CNES/DATASUS</h4>
            <p style="margin:8px 0 0;color:#555;font-size:14px">
              Todos os estabelecimentos públicos registrados, com leitos SUS e tipo de gestão.
            </p>
          </div>
          <div style="background:#FFF3E0;padding:20px;border-radius:10px;border-left:4px solid #E65100">
            <h4 style="margin:0;color:#E65100">⭐ Score de potencial</h4>
            <p style="margin:8px 0 0;color:#555;font-size:14px">
              Algoritmo de scoring prioriza estabelecimentos com maior potencial de consumo
              de medicamentos de alto custo.
            </p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ── Rodapé ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <hr style="margin-top:40px">
    <p style="text-align:center;color:#aaa;font-size:12px">
      Health Route Intelligence · Dados: CNES/DATASUS + Google Maps Platform ·
      Uso exclusivo para fins comerciais e de planejamento estratégico
    </p>
    """,
    unsafe_allow_html=True,
)