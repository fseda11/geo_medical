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

# ── Neighborhood autocomplete (inline para evitar dependência de geocoding) ───
def search_neighborhoods_autocomplete(query: str, city_lat: float = 0, city_lng: float = 0) -> list:
    import requests
    from config import GOOGLE_API_KEY, GMAPS_PLACES_AC_URL
    if len(query) < 2:
        return []
    params = {"input": query, "types": "(regions)", "language": "pt-BR",
              "components": "country:br", "key": GOOGLE_API_KEY}
    if city_lat and city_lng:
        params["location"] = f"{city_lat},{city_lng}"
        params["radius"]   = "30000"
    try:
        resp = requests.get(GMAPS_PLACES_AC_URL, params=params, timeout=8)
        return [(p["description"], p["place_id"]) for p in resp.json().get("predictions", [])]
    except Exception:
        return []


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

    _city_lat = st.session_state.get("origin_lat", 0)
    _city_lng = st.session_state.get("origin_lng", 0)
    def _search_bairro(q):
        return search_neighborhoods_autocomplete(q, _city_lat, _city_lng)
    selected_bairro = st_searchbox(
        search_function=_search_bairro,
        placeholder="Bairro (opcional — padrão: centro da cidade)",
        label="Bairro de origem",
        key="bairro_searchbox",
    )

    st.markdown("---")
    st.markdown("### 🔧 Filtros de estabelecimento")

    filter_categories = st.multiselect(
        "Categorias",
        options=["hospital", "upa", "clinica", "farmacia", "ubs", "secretaria", "outro"],
        default=["hospital", "upa", "clinica", "farmacia", "ubs", "secretaria", "outro"],
        format_func=lambda c: f"{CATEGORY_ICONS.get(c, '🏢')} {c.capitalize()}",
    )

    especialidade_filter = st.multiselect(
        "🔬 Especialidade",
        options=['Cardiologia', 'Neurologia', 'Oncologia', 'Ortopedia', 'Pediatria', 'Ginecologia', 'Oftalmologia', 'Dermatologia', 'Psiquiatria', 'Endocrinologia', 'Nefrologia', 'Reumatologia', 'Gastroenterologia', 'Pneumologia', 'Urologia', 'Infectologia', 'Hematologia', 'Geriatria', 'Nutrologia', 'Fisioterapia'],
        default=[],
        placeholder="Todas as especialidades",
        help="Filtra por especialidade no nome do estabelecimento.",
    )

    only_relevant = st.checkbox(
        "Apenas relevantes para alto custo",
        value=False,
        help="Filtra hospitais, clínicas de especialidade, UPAs, farmácias, hospital dia.",
    )

    min_score = st.slider("Score mínimo de potencial", 0, 80, 0, 5)

    st.markdown("---")

    search_btn = st.button(
        "🚀 Buscar estabelecimentos",
        type="primary",
        use_container_width=True,
    )

    if st.session_state.get("show_results", False):
        if st.button("🏠 Tela inicial  (mantém pesquisa)", use_container_width=True):
            st.session_state["show_results"] = False
            st.rerun()

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
        if especialidade_filter:
            import re as _re
            pat = "|".join(_re.escape(e) for e in especialidade_filter)
            mask = (
                establishments["no_razao_social"].str.contains(pat, case=False, na=False) |
                establishments["no_fantasia"].str.contains(pat, case=False, na=False) |
                establishments["ds_tipo_unidade"].str.contains(pat, case=False, na=False)
            )
            establishments = establishments[mask]

    # ── Monta mapa ────────────────────────────────────────────────────────────
    with st.spinner("🗺️ Construindo mapa…"):
        fmap = build_map(
            origin=origin,
            municipalities=municipalities,
            establishments=establishments,
            max_km=distance_km,
            draw_routes_to=999,  # traça rotas para todos os municípios (cacheadas)
        )

    # Salva na sessão
    st.session_state.result_map   = fmap
    st.session_state.result_df    = establishments
    st.session_state.result_munis = municipalities
    st.session_state.origin_data  = origin
    st.session_state["origin_lat"] = origin["lat"]
    st.session_state["origin_lng"] = origin["lng"]
    if selected_bairro:
        _bq = selected_bairro
        _bo = None
        if isinstance(_bq, tuple): _bo = geocode_by_place_id(_bq[1])
        elif isinstance(_bq, str) and len(_bq) > 3: _bo = geocode_by_text(_bq)
        if _bo:
            origin["lat"] = _bo["lat"]
            origin["lng"] = _bo["lng"]
    st.session_state["show_results"] = True

# ── Exibição dos resultados ───────────────────────────────────────────────────
if st.session_state.result_map is not None and st.session_state.get("show_results", False):
    origin        = st.session_state.origin_data
    municipalities = st.session_state.result_munis
    establishments = st.session_state.result_df
    summary        = summarize_establishments(establishments)

    # ── Métricas principais ───────────────────────────────────────────────────
    n_corredor = len(municipalities)
    n_com_cnes = summary.get("municipios", 0)
    delta_muni = f"{n_com_cnes} com dados CNES" if n_com_cnes < n_corredor else "Todos com dados CNES"

    cols = st.columns(8)
    metrics = [
        ("🏙️ Municípios",    n_corredor,                        delta_muni),
        ("🏥 Hospitais",      summary.get("hospitais",      0),  None),
        ("🏨 Clínicas",       summary.get("clinicas",       0),  None),
        ("🚨 UPAs",           summary.get("upas",           0),  None),
        ("💊 Farmácias",      summary.get("farmacias",      0),  None),
        ("🩺 UBS / Postos",   summary.get("ubs",            0),  None),
        ("🏢 Outros",         summary.get("outros",         0),  None),
        ("⭐ Alto potencial", summary.get("alto_potencial", 0),  None),
    ]
    for col, (label, val, delta) in zip(cols, metrics):
        col.metric(label, f"{val:,}", delta=delta)

    st.markdown("---")
    st.markdown("---")

    # ── Mapa full-width ──────────────────────────────────────────────────────
    st.markdown("#### 🗺️ Mapa de cobertura")
    _c1, _c2, _c3, _c4 = st.columns([2, 1, 1, 1])
    with _c1:
        st.caption("Filtrar por potencial:")
    with _c2:
        _fa = st.checkbox("🟢 Alto (≥ 60)",   value=True, key="fa")
    with _c3:
        _fm = st.checkbox("🟠 Médio (40–59)", value=True, key="fm")
    with _c4:
        _fb = st.checkbox("⚫ Baixo (< 40)",  value=True, key="fb")

    _est_map = establishments.copy()
    if not (_fa and _fm and _fb):
        import functools, operator as _op
        _mk = []
        if _fa: _mk.append(_est_map["score_potencial"] >= 60)
        if _fm: _mk.append((_est_map["score_potencial"] >= 40) & (_est_map["score_potencial"] < 60))
        if _fb: _mk.append(_est_map["score_potencial"] < 40)
        _est_map = _est_map[functools.reduce(_op.or_, _mk)] if _mk else _est_map.iloc[0:0]

    with st.spinner("🗺️ Atualizando mapa…"):
        from map_builder import build_map as _bm
        _fmap_f = _bm(
            origin=st.session_state.origin_data,
            municipalities=st.session_state.result_munis,
            establishments=_est_map,
            max_km=distance_km,
            draw_routes_to=999,
        )
    st.caption("💡 Controle de camadas ▶ (canto superior direito) para ativar/desativar categorias.")
    st_folium(_fmap_f, use_container_width=True, height=640, returned_objects=[])

    st.markdown("---")

    # ── Tabela única ──────────────────────────────────────────────────────────
    if not establishments.empty:
        RENAME = {
            "score_potencial":    "⭐ Score",
            "co_cnes":            "Cód. CNES",
            "co_cnpj":            "CNPJ",
            "no_razao_social":    "Razão Social",
            "no_fantasia":        "Nome Fantasia",
            "ds_tipo_unidade":    "Tipo",
            "municipio_nome":     "Município",
            "uf":                 "UF",
            "no_logradouro":      "Endereço",
            "nu_endereco":        "Número",
            "no_bairro":          "Bairro",
            "co_cep":             "CEP",
            "road_km":            "Dist. (km)",
            "duration_text":      "Tempo",
            "nu_telefone_cnes":   "Telefone (CNES)",
            "nu_telefone_google": "Tel. CNPJ / Google",
            "no_email":           "E-mail",
            "tp_gestao":          "Gestão",
            "natureza_juridica":  "Natureza Jurídica",
            "turno_atendimento":  "Turno",
            "atend_sus":          "Atend. SUS",
            "tem_cirurgia":       "Ctr. Cirúrgico",
            "tem_obstetrico":     "Ctr. Obstétrico",
            "dt_atualizacao":     "Atualização",
        }
        _drop = ["latitude","longitude","category","tp_unidade","tp_pfpj",
                 "qt_leito_internacao","qt_leito_sus","atend_ambulatorial",
                 "nu_telefone","ds_natureza_juridica"]

        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            q = st.text_input("🔎 Filtrar por nome…", key="tab_search")
        df_f = establishments
        if q:
            df_f = df_f[df_f["no_razao_social"].str.contains(q, case=False, na=False)]

        df_d = df_f.drop(columns=_drop, errors="ignore")
        df_d = df_d.rename(columns={k:v for k,v in RENAME.items() if k in df_d.columns})

        slug = origin.get("formatted_address","busca").split(",")[0].strip().replace(" ","_").lower()

        with c2:
            try:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as w:
                    df_d.to_excel(w, sheet_name="Estabelecimentos", index=False)
                    if municipalities is not None:
                        municipalities.to_excel(w, sheet_name="Municípios", index=False)
                buf.seek(0)
                st.download_button("⬇️ Excel", data=buf,
                    file_name=f"health_route_{slug}_{distance_km}km.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)
            except Exception:
                st.warning("Instale openpyxl")
        with c3:
            st.download_button("⬇️ CSV",
                data=df_d.to_csv(index=False).encode("utf-8"),
                file_name=f"health_route_{slug}_{distance_km}km.csv",
                mime="text/csv", use_container_width=True)

        st.dataframe(df_d, use_container_width=True, height=520, hide_index=True,
            column_config={
                "⭐ Score": st.column_config.ProgressColumn("⭐ Score", min_value=0, max_value=100, format="%d"),
                "Dist. (km)": st.column_config.NumberColumn(format="%.1f km"),
            })
        st.caption(f"{len(df_f):,} estabelecimento(s) exibidos · {len(establishments):,} total")
    else:
        st.info("Nenhum estabelecimento após aplicar os filtros.")

    st.markdown("---")
    with st.expander("🏙️ Municípios na rota", expanded=False):
        if municipalities is not None and not municipalities.empty:
            _cm = {"nome":"Município","uf":"UF","estado":"Estado","road_km":"Dist. (km)","duration_text":"Tempo"}
            _ex = [c for c in _cm if c in municipalities.columns]
            st.dataframe(municipalities[_ex].rename(columns=_cm).reset_index(drop=True),
                         use_container_width=True, height=350, hide_index=True)
            st.caption(f"{len(municipalities)} municípios · {distance_km} km por rodovias")


else:
    has_prev = st.session_state.result_map is not None
    if has_prev:
        st.info("💾 Pesquisa salva em memória. Clique em **Buscar** para refazer ou veja os resultados abaixo.", icon="💾")
        if st.button("📊 Ver resultados da última pesquisa", type="primary"):
            st.session_state["show_results"] = True
            st.rerun()
    else:
        st.info("👈 Configure a busca na barra lateral e clique em **Buscar estabelecimentos**.", icon="🗺️")
    st.markdown("""
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:20px">
      <div style="background:#E3F2FD;padding:20px;border-radius:10px;border-left:4px solid #1565C0">
        <h4 style="margin:0;color:#1565C0">🛣️ Rotas reais</h4>
        <p style="margin:8px 0 0;color:#555;font-size:14px">Distâncias por Google Distance Matrix — rodovias reais, não raio simples. Filtro de corredor mantém só municípios ao longo das estradas.</p>
      </div>
      <div style="background:#E8F5E9;padding:20px;border-radius:10px;border-left:4px solid #2E7D32">
        <h4 style="margin:0;color:#2E7D32">🏥 Dados CNES/DATASUS</h4>
        <p style="margin:8px 0 0;color:#555;font-size:14px">Todos os estabelecimentos registrados: tipo, endereço, turno, gestão e natureza jurídica. Telefones via CNES, Receita Federal e Google.</p>
      </div>
      <div style="background:#FFF3E0;padding:20px;border-radius:10px;border-left:4px solid #E65100">
        <h4 style="margin:0;color:#E65100">⭐ Score de potencial</h4>
        <p style="margin:8px 0 0;color:#555;font-size:14px">Algoritmo prioriza hospitais, farmácias e clínicas com maior potencial de consumo de medicamentos de alto custo.</p>
      </div>
    </div>""", unsafe_allow_html=True)
    st.markdown("---")
    with st.expander("📊 Como é calculado o Score de Potencial?", expanded=False):
        st.markdown("""
O score vai de **0 a 100 pontos** e estima o potencial de consumo de medicamentos de alto custo:

| Fator | Critério | Pontos |
|---|---|---|
| **Tipo de unidade** | Hospital Geral / Especializado | 50 |
| | Farmácia | 40 |
| | UPA / Pronto-Socorro / Clínica de Especialidade | 30 |
| | UBS / Posto de Saúde | 10 |
| | Outros (consultórios, CAPS…) | 5 |
| **Capacidade hospitalar** | Flags de internação + cirurgia + obstetrícia | até 30 |
| **Serviços adicionais** | Centro cirúrgico, obstétrico, ambulatorial | até 10 |
| **Gestão** | Estadual / Federal | +10 · Dupla +6 · Municipal +4 |

**Interpretação:** 🟢 ≥ 60 Alto · 🟠 40–59 Médio · ⚫ < 40 Baixo

> Score é indicador de priorização comercial, não de qualidade assistencial.
        """)

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