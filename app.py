"""
app.py — Health Route Intelligence v2 — FetchMed
Plataforma de inteligência comercial para medicamentos de alto custo.

Módulos:
  1. Mapeamento CNES          — estabelecimentos por rota rodoviária
  2. Leads Complementares     — associações de pacientes + advogados de saúde
  3. Doenças Raras            — epidemiologia + oportunidades por região
  4. Inteligência Judicial    — judicialização da saúde por estado
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
from doencas_raras import (
    DOENCAS, JUDICIALIZACAO_ESTADOS, ESTADO_COORDS,
    get_disease_df, get_state_concentration, make_state_map,
    get_medicamentos_por_doenca, buscar_estabelecimentos_por_doenca,
)
from leads import buscar_associacoes, buscar_advogados, build_leads_map

# ── Configuração ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Health Route Intelligence | FetchMed",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Estado da sessão ───────────────────────────────────────────────────────────
for key, default in [
    ("result_map", None), ("result_df", None), ("result_munis", None),
    ("origin_data", None), ("show_results", False),
    ("origin_lat", 0.0), ("origin_lng", 0.0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── Cabeçalho ──────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='margin-bottom:0'>💊 Health Route Intelligence</h1>"
    "<p style='color:#888;margin-top:4px;font-size:15px'>"
    "Inteligência comercial para medicamentos de alto custo · FetchMed</p>",
    unsafe_allow_html=True,
)

# ── Tabs principais ────────────────────────────────────────────────────────────
TAB_CNES, TAB_LEADS, TAB_RARAS, TAB_JUDICIAL = st.tabs([
    "🗺️ Mapeamento CNES",
    "🤝 Leads Complementares",
    "🧬 Doenças Raras",
    "⚖️ Inteligência Judicial",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — MAPEAMENTO CNES
# ══════════════════════════════════════════════════════════════════════════════
with TAB_CNES:

    def search_neighborhoods_autocomplete(query, city_lat=0, city_lng=0):
        import requests
        if len(query) < 2:
            return []
        params = {"input": query, "types": "(regions)", "language": "pt-BR",
                  "components": "country:br", "key": GOOGLE_API_KEY}
        if city_lat and city_lng:
            params["location"] = f"{city_lat},{city_lng}"
            params["radius"]   = "30000"
        try:
            r = requests.get(
                "https://maps.googleapis.com/maps/api/place/autocomplete/json",
                params=params, timeout=8,
            )
            return [(p["description"], p["place_id"]) for p in r.json().get("predictions", [])]
        except Exception:
            return []

    SPECIALTIES = [
        "Cardiologia","Neurologia","Oncologia","Ortopedia","Pediatria","Ginecologia",
        "Oftalmologia","Dermatologia","Psiquiatria","Endocrinologia","Nefrologia",
        "Reumatologia","Gastroenterologia","Pneumologia","Urologia","Infectologia",
        "Hematologia","Geriatria","Nutrologia","Fisioterapia",
    ]

    with st.sidebar:
        st.markdown("### 🔍 Configurar busca")

        selected_city = st_searchbox(
            search_function=search_cities_autocomplete,
            placeholder="Digite o nome da cidade…",
            label="Cidade de origem",
            key="city_searchbox",
        )

        distance_km = st.number_input(
            "Distância máxima por rodovias (km)",
            min_value=1, max_value=500,
            value=DEFAULT_DISTANCE_KM, step=1,
            help="Para bairros use 2–15 km. Para regiões use 50–300 km.",
        )

        _city_lat = st.session_state.get("origin_lat", 0)
        _city_lng = st.session_state.get("origin_lng", 0)

        def _search_bairro(q):
            return search_neighborhoods_autocomplete(q, _city_lat, _city_lng)

        selected_bairro = st_searchbox(
            search_function=_search_bairro,
            placeholder="Bairro (opcional)",
            label="Bairro de origem",
            key="bairro_searchbox",
        )

        st.markdown("---")
        st.markdown("### 🔧 Filtros de estabelecimento")

        filter_categories = st.multiselect(
            "Categorias",
            options=["hospital","upa","clinica","farmacia","ubs","secretaria","outro"],
            default=["hospital","upa","clinica","farmacia","ubs","secretaria","outro"],
            format_func=lambda c: f"{CATEGORY_ICONS.get(c,'🏢')} {c.capitalize()}",
        )

        especialidade_filter = st.multiselect(
            "🔬 Especialidade",
            options=SPECIALTIES, default=[],
            placeholder="Todas as especialidades",
            help="Filtra por especialidade no nome do estabelecimento.",
        )

        only_relevant = st.checkbox(
            "Apenas relevantes para alto custo", value=False,
            help="Hospitais, clínicas de especialidade, UPAs, farmácias, hospital dia.",
        )

        min_score = st.slider("Score mínimo de potencial", 0, 80, 0, 5)

        st.markdown("---")

        search_btn = st.button("🚀 Buscar estabelecimentos", type="primary", use_container_width=True)

        if st.session_state.get("show_results", False):
            if st.button("🏠 Tela inicial  (mantém pesquisa)", use_container_width=True):
                st.session_state["show_results"] = False
                st.rerun()

    # ── Lógica de busca ───────────────────────────────────────────────────────
    if search_btn:
        if not selected_city:
            st.warning("⚠️ Selecione uma cidade de origem.")
            st.stop()

        with st.status("🗺️ Calculando rotas…", expanded=True) as status:
            prog1  = st.progress(0)
            prog_t = st.empty()

            # Geocodifica cidade
            if isinstance(selected_city, tuple):
                origin = geocode_by_place_id(selected_city[1]) or geocode_by_text(selected_city[0])
            else:
                origin = geocode_by_place_id(selected_city) or geocode_by_text(selected_city)

            if not origin:
                st.error("❌ Não foi possível geocodificar a cidade.")
                st.stop()

            st.session_state["origin_lat"] = origin["lat"]
            st.session_state["origin_lng"] = origin["lng"]

            # Ajusta origem para bairro
            if selected_bairro:
                bq = selected_bairro
                bo = None
                if isinstance(bq, tuple):  bo = geocode_by_place_id(bq[1])
                elif isinstance(bq, str) and len(bq) > 3: bo = geocode_by_text(bq)
                if bo:
                    origin["lat"] = bo["lat"]
                    origin["lng"] = bo["lng"]

            # Municípios
            municipalities = get_reachable_municipalities(
                origin["lat"], origin["lng"], distance_km,
                progress_bar=prog1, progress_text_slot=prog_t,
            )
            if municipalities.empty:
                st.error("❌ Nenhum município encontrado.")
                st.stop()

            # CNES
            prog2  = st.progress(0)
            prog_t2 = st.empty()
            establishments = get_establishments_for_municipalities(
                municipalities=municipalities,
                only_relevant=only_relevant,
                progress_bar=prog2,
                progress_text_slot=prog_t2,
            )

            # Filtros
            if not establishments.empty:
                if filter_categories:
                    establishments = establishments[establishments["category"].isin(filter_categories)]
                if min_score > 0:
                    establishments = establishments[establishments["score_potencial"] >= min_score]
                if especialidade_filter:
                    import re as _re
                    pat = "|".join(_re.escape(e) for e in especialidade_filter)
                    mask = (
                        establishments["no_razao_social"].str.contains(pat, case=False, na=False) |
                        establishments["no_fantasia"].str.contains(pat, case=False, na=False)
                    )
                    establishments = establishments[mask]

            # Mapa
            _urban = distance_km < 30
            import pandas as _pd
            if _urban and not establishments.empty:
                _top = establishments.nlargest(min(50, len(establishments)), "score_potencial")
                _top = _top.dropna(subset=["latitude","longitude"]).copy()
                _top["_lr"] = _top["latitude"].astype(float).round(3)
                _top["_lg"] = _top["longitude"].astype(float).round(3)
                _top = _top.drop_duplicates(subset=["_lr","_lg"]).head(15)
                _mm  = _pd.DataFrame({
                    "latitude":_top["latitude"].values, "longitude":_top["longitude"].values,
                    "nome":_top["no_razao_social"].values, "road_km":_top["road_km"].values,
                })
                _rn = len(_mm)
            else:
                _mm, _rn = municipalities, 999

            prog_t2.text("🗺️ Construindo mapa…")
            fmap = build_map(
                origin=origin, municipalities=_mm,
                establishments=establishments,
                max_km=distance_km, draw_routes_to=_rn,
            )
            if _urban:
                fmap.zoom_start = 14
                fmap.location   = [origin["lat"], origin["lng"]]

            st.session_state.result_map   = fmap
            st.session_state.result_df    = establishments
            st.session_state.result_munis = municipalities
            st.session_state.origin_data  = origin
            st.session_state["show_results"]  = True
            st.session_state["origin_lat"]    = origin["lat"]
            st.session_state["origin_lng"]    = origin["lng"]
            status.update(label="✅ Concluído!", state="complete")

    # ── Exibição de resultados ─────────────────────────────────────────────────
    if st.session_state.result_map and st.session_state.get("show_results"):
        establishments = st.session_state.result_df
        municipalities = st.session_state.result_munis
        origin         = st.session_state.origin_data
        distance_km    = st.number_input(
            "Distância máxima por rodovias (km)",
            min_value=1, max_value=500, value=DEFAULT_DISTANCE_KM, step=1,
            key="_dist_display", label_visibility="hidden",
        ) if False else distance_km

        summary = summarize_establishments(establishments) if not establishments.empty else {}
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

        # Filtros acima do mapa
        st.markdown("#### 🗺️ Mapa de cobertura")
        _map_espec = st.multiselect(
            "🔬 Especialidade no mapa", options=SPECIALTIES, default=[],
            placeholder="Todas as especialidades", key="map_espec",
        )
        _c1,_c2,_c3,_c4 = st.columns([2,1,1,1])
        with _c1: st.caption("Filtrar por potencial:")
        with _c2: _fa = st.checkbox("🟢 Alto (≥ 60)",   value=True, key="fa")
        with _c3: _fm = st.checkbox("🟠 Médio (40–59)", value=True, key="fm")
        with _c4: _fb = st.checkbox("⚫ Baixo (< 40)",  value=True, key="fb")

        _est_map = establishments.copy()
        if not (_fa and _fm and _fb):
            import functools, operator as _op
            _mk = []
            if _fa: _mk.append(_est_map["score_potencial"] >= 60)
            if _fm: _mk.append((_est_map["score_potencial"] >= 40) & (_est_map["score_potencial"] < 60))
            if _fb: _mk.append(_est_map["score_potencial"] < 40)
            _est_map = _est_map[functools.reduce(_op.or_, _mk)] if _mk else _est_map.iloc[0:0]
        if _map_espec:
            import re as _re2
            _pat2 = "|".join(_re2.escape(e) for e in _map_espec)
            _emask = (
                _est_map["no_razao_social"].str.contains(_pat2, case=False, na=False) |
                _est_map["no_fantasia"].str.contains(_pat2, case=False, na=False)
            )
            _est_map = _est_map[_emask]

        with st.spinner("🗺️ Atualizando mapa…"):
            from map_builder import build_map as _bm
            import pandas as _pd2
            _urban2 = (distance_km if isinstance(distance_km, (int,float)) else DEFAULT_DISTANCE_KM) < 30
            if _urban2 and not _est_map.empty:
                _top2 = _est_map.nlargest(min(50, len(_est_map)), "score_potencial")
                _top2 = _top2.dropna(subset=["latitude","longitude"]).copy()
                _top2["_lr"] = _top2["latitude"].astype(float).round(3)
                _top2["_lg"] = _top2["longitude"].astype(float).round(3)
                _top2 = _top2.drop_duplicates(subset=["_lr","_lg"]).head(15)
                _mm2  = _pd2.DataFrame({
                    "latitude":_top2["latitude"].values, "longitude":_top2["longitude"].values,
                    "nome":_top2["no_razao_social"].values, "road_km":_top2["road_km"].values,
                })
                _rn2  = len(_mm2)
            else:
                _mm2, _rn2 = st.session_state.result_munis, 999
            _fmap_f = _bm(
                origin=origin, municipalities=_mm2,
                establishments=_est_map, max_km=DEFAULT_DISTANCE_KM, draw_routes_to=_rn2,
            )

        st.caption("💡 Controle de camadas ▶ (canto superior direito) para ativar/desativar categorias.")
        st_folium(_fmap_f, use_container_width=True, height=640, returned_objects=[])

        st.markdown("---")

        # Tabela
        RENAME = {
            "score_potencial":"⭐ Score","co_cnes":"Cód. CNES","co_cnpj":"CNPJ",
            "no_razao_social":"Razão Social","no_fantasia":"Nome Fantasia",
            "ds_tipo_unidade":"Tipo","municipio_nome":"Município","uf":"UF",
            "no_logradouro":"Endereço","nu_endereco":"Número","no_bairro":"Bairro",
            "co_cep":"CEP","road_km":"Dist. (km)","duration_text":"Tempo",
            "nu_telefone_cnes":"Telefone (CNES)","nu_telefone_google":"Tel. CNPJ / Google",
            "no_email":"E-mail","tp_gestao":"Gestão","natureza_juridica":"Natureza Jurídica",
            "turno_atendimento":"Turno","atend_sus":"Atend. SUS",
            "tem_cirurgia":"Ctr. Cirúrgico","tem_obstetrico":"Ctr. Obstétrico",
            "dt_atualizacao":"Atualização",
        }
        _drop = ["latitude","longitude","category","tp_unidade","tp_pfpj",
                 "qt_leito_internacao","qt_leito_sus","atend_ambulatorial",
                 "nu_telefone","ds_natureza_juridica","coords_from_cnes"]

        c1,c2,c3 = st.columns([3,1,1])
        with c1: q = st.text_input("🔎 Filtrar por nome…", key="tab_search")
        df_f = establishments
        if q:
            df_f = df_f[df_f["no_razao_social"].str.contains(q, case=False, na=False)]

        df_d = df_f.drop(columns=_drop, errors="ignore")
        df_d = df_d.rename(columns={k:v for k,v in RENAME.items() if k in df_d.columns})
        slug  = origin.get("formatted_address","busca").split(",")[0].strip().replace(" ","_").lower()

        with c2:
            try:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as w:
                    df_d.to_excel(w, sheet_name="Estabelecimentos", index=False)
                    municipalities.to_excel(w, sheet_name="Municípios", index=False)
                buf.seek(0)
                st.download_button("⬇️ Excel", data=buf,
                    file_name=f"fetchmed_{slug}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)
            except Exception:
                pass
        with c3:
            st.download_button("⬇️ CSV", data=df_d.to_csv(index=False).encode("utf-8"),
                file_name=f"fetchmed_{slug}.csv", mime="text/csv", use_container_width=True)

        st.dataframe(df_d, use_container_width=True, height=520, hide_index=True,
            column_config={
                "⭐ Score": st.column_config.ProgressColumn("⭐ Score", min_value=0, max_value=100, format="%d"),
                "Dist. (km)": st.column_config.NumberColumn(format="%.1f km"),
            })
        st.caption(f"{len(df_f):,} estabelecimentos · {len(establishments):,} total")

        st.markdown("---")
        with st.expander("🏙️ Municípios na rota", expanded=False):
            _cm = {"nome":"Município","uf":"UF","estado":"Estado","road_km":"Dist. (km)","duration_text":"Tempo"}
            _ex = [c for c in _cm if c in municipalities.columns]
            st.dataframe(municipalities[_ex].rename(columns=_cm).reset_index(drop=True),
                         use_container_width=True, height=300, hide_index=True)

    else:
        has_prev = st.session_state.result_map is not None
        if has_prev:
            st.info("💾 Pesquisa salva. Clique em **Buscar** para refazer.", icon="💾")
            if st.button("📊 Ver resultados da última pesquisa", type="primary"):
                st.session_state["show_results"] = True
                st.rerun()
        else:
            st.info("👈 Configure a busca na barra lateral.", icon="🗺️")

        st.markdown("""
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:20px">
          <div style="background:#E3F2FD;padding:20px;border-radius:10px;border-left:4px solid #1565C0">
            <h4 style="margin:0;color:#1565C0">🛣️ Rotas reais</h4>
            <p style="margin:8px 0 0;color:#555;font-size:14px">Distâncias por Google Distance Matrix — rodovias reais, não raio simples.</p>
          </div>
          <div style="background:#E8F5E9;padding:20px;border-radius:10px;border-left:4px solid #2E7D32">
            <h4 style="margin:0;color:#2E7D32">🏥 Dados CNES/DATASUS</h4>
            <p style="margin:8px 0 0;color:#555;font-size:14px">Estabelecimentos com score de potencial, telefones e endereços.</p>
          </div>
          <div style="background:#FFF3E0;padding:20px;border-radius:10px;border-left:4px solid #E65100">
            <h4 style="margin:0;color:#E65100">⭐ Score de potencial</h4>
            <p style="margin:8px 0 0;color:#555;font-size:14px">Algoritmo prioriza hospitais e farmácias com maior consumo de alto custo.</p>
          </div>
        </div>""", unsafe_allow_html=True)

        st.markdown("---")
        with st.expander("📊 Como é calculado o Score de Potencial?", expanded=False):
            st.markdown("""
| Fator | Critério | Pontos |
|---|---|---|
| **Tipo de unidade** | Hospital Geral / Especializado | 50 |
| | Farmácia | 40 |
| | UPA / Clínica de Especialidade | 30 |
| | UBS / Posto | 10 |
| **Capacidade** | Internação + cirurgia + obstetrícia | até 30 |
| **Serviços** | Centro cirúrgico, obstétrico, ambulatorial | até 10 |
| **Gestão** | Estadual/Federal +10 · Dupla +6 · Municipal +4 | até 10 |

🟢 ≥ 60 Alto · 🟠 40–59 Médio · ⚫ < 40 Baixo
            """)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — LEADS COMPLEMENTARES
# ══════════════════════════════════════════════════════════════════════════════
with TAB_LEADS:
    st.markdown("### 🤝 Leads Complementares")
    st.markdown(
        "Encontre **associações de pacientes**, **ONGs de saúde** e "
        "**advogados especializados em direito à saúde** na área de interesse. "
        "Esses perfis são agentes-chave no acesso a medicamentos de alto custo — "
        "o advogado viabiliza o processo judicial, a associação conecta ao paciente."
    )

    col_l, col_r = st.columns([1, 2])
    with col_l:
        st.markdown("#### ⚙️ Parâmetros")
        leads_city = st.text_input("Cidade", placeholder="Ex: São Paulo", key="leads_city")
        leads_radius = st.slider("Raio de busca (km)", 10, 200, 50, 10, key="leads_radius")
        leads_doenca = st.selectbox(
            "Filtrar por doença (opcional)",
            options=[""] + list(DOENCAS.keys()),
            format_func=lambda x: "Todas as doenças" if x == "" else x,
            key="leads_doenca",
        )
        buscar_leads = st.button("🔍 Buscar leads", type="primary", key="btn_leads")

    if buscar_leads and leads_city:
        with st.spinner("🔍 Buscando leads via Google Places…"):
            geo = geocode_by_text(f"{leads_city}, Brasil")
            if not geo:
                st.error("Cidade não encontrada.")
            else:
                lat, lng = geo["lat"], geo["lng"]
                assocs   = buscar_associacoes(lat, lng, leads_radius, leads_doenca)
                advs     = buscar_advogados(lat, lng, leads_radius)
                all_leads = assocs + advs
                st.session_state["leads_data"]   = all_leads
                st.session_state["leads_origin"] = geo

    if "leads_data" in st.session_state and st.session_state["leads_data"]:
        all_leads = st.session_state["leads_data"]
        geo       = st.session_state["leads_origin"]
        assocs_d  = [l for l in all_leads if "Assoc" in l.get("tipo","")]
        advs_d    = [l for l in all_leads if "Advog" in l.get("tipo","")]

        with col_r:
            st.markdown(f"#### 🗺️ Mapa — {len(all_leads)} leads encontrados")
            leads_map = build_leads_map(all_leads, geo["lat"], geo["lng"])
            st_folium(leads_map, use_container_width=True, height=480, returned_objects=[])

        st.markdown("---")
        t_assoc, t_adv = st.tabs([
            f"🤝 Associações / ONGs ({len(assocs_d)})",
            f"⚖️ Advogados / Escritórios ({len(advs_d)})",
        ])

        def leads_to_df(leads_list):
            return pd.DataFrame([{
                "Nome":      l["nome"],
                "Endereço":  l["endereco"],
                "Telefone":  l["telefone"] or "—",
                "Website":   l["website"] or "—",
                "Avaliação": l.get("avaliacao","") or "—",
            } for l in leads_list])

        with t_assoc:
            if assocs_d:
                st.dataframe(leads_to_df(assocs_d), use_container_width=True, hide_index=True, height=350)
                st.download_button("⬇️ Exportar CSV",
                    data=leads_to_df(assocs_d).to_csv(index=False).encode("utf-8"),
                    file_name="associacoes.csv", mime="text/csv")
            else:
                st.info("Nenhuma associação encontrada no raio selecionado.")

        with t_adv:
            if advs_d:
                df_adv = leads_to_df(advs_d)
                st.dataframe(df_adv, use_container_width=True, hide_index=True, height=350)
                st.download_button("⬇️ Exportar CSV",
                    data=df_adv.to_csv(index=False).encode("utf-8"),
                    file_name="advogados.csv", mime="text/csv")
                st.info(
                    "💡 **Estratégia FetchMed**: abordar advogados especializados antes da sentença. "
                    "Com o orçamento da FetchMed nos autos, a distribuição é direcionada desde o início do processo.",
                    icon="💡",
                )
            else:
                st.info("Nenhum advogado encontrado no raio selecionado.")
    elif "leads_data" not in st.session_state:
        with col_r:
            st.info("👈 Configure os parâmetros e clique em **Buscar leads**.", icon="🤝")
            st.markdown("""
            **Por que esses perfis importam:**
            - **Associações de pacientes** conectam diretamente ao paciente final e influenciam a escolha de distribuidora.
            - **Advogados de saúde** são o canal de acesso para pacientes que não conseguem o medicamento pelo SUS — 
              ~40% das ações judiciais em saúde envolvem medicamentos de alto custo.
            - A FetchMed pode ser indicada pelo advogado desde a fase de instrução processual, 
              incluindo o orçamento nos autos antes da sentença.
            """)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — DOENÇAS RARAS
# ══════════════════════════════════════════════════════════════════════════════
with TAB_RARAS:
    st.markdown("### 🧬 Doenças Raras — Inteligência Epidemiológica")
    st.markdown(
        "Doenças raras são em sua maioria hereditárias e apresentam **concentração geográfica**. "
        "Este módulo mapeia onde estão os pacientes e quais medicamentos de alto custo são usados, "
        "permitindo **priorizar geograficamente** os esforços comerciais da FetchMed."
    )

    col_dr1, col_dr2 = st.columns([1, 2])

    with col_dr1:
        st.markdown("#### 🔍 Selecione a doença")
        cat_options = sorted(set(d["categoria"].split(" / ")[0] for d in DOENCAS.values()))
        cat_filter  = st.selectbox("Categoria", ["Todas"] + cat_options, key="dr_cat")

        filtered_diseases = {
            k: v for k, v in DOENCAS.items()
            if cat_filter == "Todas" or v["categoria"].startswith(cat_filter)
        }
        doenca_sel = st.selectbox("Doença", list(filtered_diseases.keys()), key="dr_sel")

    if doenca_sel:
        d = DOENCAS[doenca_sel]
        with col_dr1:
            st.markdown("---")
            jud_color = {"Muito Alta":"🔴","Alta":"🟠","Média":"🟡","Baixa":"🟢"}.get(d["judicializacao"],"⚫")
            st.metric("Estimativa Brasil", f"{d['estimativa_br']:,} pacientes")
            st.metric("Prevalência", f"{d['prevalencia_100k']} por 100k hab.")
            st.metric("Judicialização", f"{jud_color} {d['judicializacao']}")
            st.markdown(f"**CIDs:** {d['cids']}")
            st.markdown(f"**Categoria:** {d['categoria']}")
            st.info(d["perfil"], icon="📋")

        with col_dr2:
            st.markdown(f"#### 🗺️ Concentração por estado — {doenca_sel}")
            dr_map = make_state_map(doenca_sel)
            st_folium(dr_map, use_container_width=True, height=420, returned_objects=[])
            st.caption("Estimativa baseada em prevalência epidemiológica publicada. "
                       "Integração com DATASUS/SINAN em desenvolvimento para dados oficiais por estado.")

        st.markdown("---")
        c_med, c_bar = st.columns([1, 1])

        with c_med:
            st.markdown("#### 💊 Medicamentos associados (ANVISA)")
            meds = get_medicamentos_por_doenca(doenca_sel)
            meds_df = pd.DataFrame([{
                "Medicamento":     m["nome"],
                "Princípio Ativo": m["principio_ativo"],
                "Via de Acesso":   m["via"],
            } for m in meds])
            st.dataframe(meds_df, use_container_width=True, hide_index=True)
            st.caption("Fonte: ANVISA dados abertos — medicamentos com registro ativo no Brasil.")

        with c_bar:
            st.markdown("#### 📊 Distribuição por estado")
            state_df, fonte_dist = get_state_concentration(doenca_sel)
            if not state_df.empty:
                import plotly.express as px_
                fig = px_.bar(
                    state_df, x="UF", y="Estimativa Pacientes",
                    color="% Nacional",
                    color_continuous_scale=["#1565C0","#FF9800","#D32F2F"],
                    text="Estimativa Pacientes",
                    height=320,
                )
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#fff", showlegend=False, margin=dict(l=0,r=0,t=20,b=0),
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"Fonte: {fonte_dist}")

        st.markdown("---")
        with st.expander("🏥 Estabelecimentos especializados por UF", expanded=False):
            uf_sel = st.selectbox("Estado", list(ESTADO_COORDS.keys()), key="dr_uf_esp")
            if st.button("Buscar estabelecimentos", key="btn_esp"):
                with st.spinner("Consultando CNES..."):
                    df_esp = buscar_estabelecimentos_por_doenca(doenca_sel, uf_sel)
                if not df_esp.empty:
                    st.dataframe(
                        df_esp[["nome", "municipio", "co_cnes"]],
                        use_container_width=True, hide_index=True,
                    )
                    st.caption(f"Fonte: CNES/DATASUS — {len(df_esp)} estabelecimentos com serviço relacionado")
                else:
                    st.info("Nenhum estabelecimento encontrado via CNES para este serviço/UF.")

    st.markdown("---")
    st.markdown("#### 📋 Visão geral — todas as doenças monitoradas")
    full_df = get_disease_df()
    st.dataframe(full_df, use_container_width=True, hide_index=True, height=420,
        column_config={
            "Judicialização": st.column_config.TextColumn("Judicial."),
            "Estimativa BR":  st.column_config.NumberColumn("Est. BR", format="%d pac."),
        })
    st.download_button(
        "⬇️ Exportar base de doenças",
        data=get_disease_df().to_csv(index=False).encode("utf-8"),
        file_name="doencas_raras_fetchmed.csv", mime="text/csv",
    )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — INTELIGÊNCIA JUDICIAL
# ══════════════════════════════════════════════════════════════════════════════
with TAB_JUDICIAL:
    st.markdown("### ⚖️ Inteligência Judicial em Saúde")
    st.markdown(
        "A judicialização é um dos principais canais de acesso a medicamentos de alto custo no Brasil. "
        "Compreender onde as ações são mais frequentes e qual o perfil dos processos é essencial "
        "para posicionar a FetchMed estrategicamente."
    )

    # KPIs nacionais
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Processos/ano (saúde)", "~400.000", "Brasil")
    c2.metric("Envolvem medicamentos", "~40%", "~160.000 casos/ano")
    c3.metric("Taxa de vitória (pac.)", "~68%", "média nacional")
    c4.metric("Tempo médio até acesso", "6–18 meses", "liminar: 30–90 dias")

    st.caption(
        "Fontes dos indicadores: "
        "CNJ *Justiça em Números* 2020 · "
        "INSPER *Judicialização da Saúde no Brasil* 2019 · "
        "CONASS *Nota Técnica* 2021. "
        "Dados nacionais agregados — valores aproximados."
    )

    st.markdown("---")

    jud_cols = st.columns([3, 2])

    with jud_cols[0]:
        st.markdown("#### 📊 Judicialização por estado")
        import plotly.express as px

        jud_df = pd.DataFrame([
            {"Estado":"SP","Processos Medicamentos":45000,"Total Saúde":107000,"% Medicamentos":42},
            {"Estado":"MG","Processos Medicamentos":22000,"Total Saúde":58000, "% Medicamentos":38},
            {"Estado":"RJ","Processos Medicamentos":18000,"Total Saúde":40000, "% Medicamentos":45},
            {"Estado":"RS","Processos Medicamentos":15000,"Total Saúde":27000, "% Medicamentos":55},
            {"Estado":"SC","Processos Medicamentos":9000, "Total Saúde":17000, "% Medicamentos":52},
            {"Estado":"PR","Processos Medicamentos":11000,"Total Saúde":23000, "% Medicamentos":48},
            {"Estado":"BA","Processos Medicamentos":8000, "Total Saúde":23000, "% Medicamentos":35},
            {"Estado":"CE","Processos Medicamentos":6000, "Total Saúde":18000, "% Medicamentos":33},
            {"Estado":"PE","Processos Medicamentos":5500, "Total Saúde":15000, "% Medicamentos":36},
            {"Estado":"GO","Processos Medicamentos":7000, "Total Saúde":18000, "% Medicamentos":40},
            {"Estado":"DF","Processos Medicamentos":5000, "Total Saúde":11000, "% Medicamentos":44},
            {"Estado":"MT","Processos Medicamentos":3500, "Total Saúde":8500,  "% Medicamentos":41},
            {"Estado":"MS","Processos Medicamentos":2800, "Total Saúde":6500,  "% Medicamentos":43},
            {"Estado":"PA","Processos Medicamentos":3000, "Total Saúde":11000, "% Medicamentos":28},
            {"Estado":"AM","Processos Medicamentos":2500, "Total Saúde":8000,  "% Medicamentos":30},
        ]).sort_values("Processos Medicamentos", ascending=False)
        _dj_fonte = "Fonte: CNJ Justiça em Números 2020 + INSPER 2019"

        fig_jud = px.bar(
            jud_df, x="Estado", y="Processos Medicamentos",
            color="% Medicamentos",
            color_continuous_scale=["#1565C0", "#D32F2F"],
            text="Processos Medicamentos",
            labels={"% Medicamentos": "% Med."},
            height=380,
        )

        fig_jud.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#fff", margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_jud, use_container_width=True)
        st.dataframe(jud_df.reset_index(drop=True), use_container_width=True, hide_index=True, height=300)
        st.caption(f"Fonte: {_dj_fonte}")
        st.download_button(
            "⬇️ Exportar dados",
            data=jud_df.to_csv(index=False).encode("utf-8"),
            file_name="judicializacao_saude_brasil.csv", mime="text/csv",
        )

    with jud_cols[1]:
        st.markdown("#### 🏛️ Fluxo judicial — como o paciente acessa")
        st.markdown("""
```
1. DIAGNÓSTICO CONFIRMADO
        ↓
2. SOLICITAÇÃO AO SUS
   (CNES / farmácia pública)
        ↓  negado ou demora
3. ADVOGADO DE SAÚDE
   (contato com escritório especializado)
        ↓
4. AÇÃO JUDICIAL
   (vara da fazenda pública / vara cível)
        ↓  liminar: 30–90 dias
5. LIMINAR CONCEDIDA
   ← FetchMed entra aqui com orçamento
        ↓
6. COMPRA DETERMINADA
   (estado obrigado a fornecer ou custeio próprio)
        ↓
7. DISTRIBUIÇÃO DO MEDICAMENTO
   ← FetchMed distribui
        ↓
8. SENTENÇA DEFINITIVA
   (tratamento contínuo assegurado)
```
        """)

        st.success(
            "**💡 Janela estratégica FetchMed**\n\n"
            "Na etapa **4 (instrução processual)**, o orçamento da distribuidora "
            "pode ser incluído nos autos. Quando o juiz determina o fornecimento, "
            "a FetchMed já está indicada no processo — direcionando a compra antes da sentença.",
            icon="💡",
        )

        st.markdown("---")
        st.markdown("#### 🔗 Acessar tribunais")
        tribunais = {
            "TJSP (São Paulo)":     "https://esaj.tjsp.jus.br/esaj/portal.do?servico=190090",
            "TJMG (Minas Gerais)":  "https://www.tjmg.jus.br/portal-tjmg/",
            "TJRJ (Rio de Janeiro)":"https://www3.tjrj.jus.br/ejud/ConsultaProcesso.aspx",
            "TJRS (Rio Grande do Sul)":"https://www.tjrs.jus.br/site/",
            "TRF-3 (Federal SP/MS)":"https://www.trf3.jus.br/trf3/",
        }
        for nome, url in tribunais.items():
            st.markdown(f"🔗 [{nome}]({url})")

    st.markdown("---")
    st.markdown("#### 📌 Fonte e metodologia")
    st.caption(
        "Dados: CNJ *Judicialização da Saúde* (2020), INSPER *Estudo sobre Demandas Judiciais* (2019), "
        "CONASS *Nota Técnica* (2021), IBGE/DATASUS. "
        "Valores aproximados para fins de inteligência comercial."
    )

# ── Rodapé ────────────────────────────────────────────────────────────────────
st.markdown(
    """<hr style="margin-top:40px">
    <p style="text-align:center;color:#888;font-size:12px">
      Health Route Intelligence v2 · FetchMed ·
      Dados: CNES/DATASUS + Google Maps Platform + Receita Federal
    </p>""",
    unsafe_allow_html=True,
)
