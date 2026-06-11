# 🏥 Health Route Intelligence

Mapeamento de estabelecimentos de saúde por **rotas rodoviárias reais** (não raio simples).  
Utiliza Google Maps Platform + API pública CNES/DATASUS.

---

## Stack

| Camada         | Tecnologia                                  |
|----------------|---------------------------------------------|
| Frontend/App   | Streamlit                                   |
| Mapa           | Folium + streamlit-folium                   |
| Autocomplete   | Google Places API (streamlit-searchbox)     |
| Distâncias     | Google Distance Matrix API                  |
| Rotas visuais  | Google Directions API                       |
| Estabelecimentos | CNES/DATASUS API pública                  |
| Municípios     | kelvins/municipios-brasileiros (CSV IBGE)   |

---

## Setup

```bash
# 1. Criar ambiente virtual
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
.venv\Scripts\activate      # Windows

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Rodar
streamlit run app.py
```

A API key já está configurada em `config.py`.

---

## Arquitetura de dados

```
Google Places Autocomplete (cidade origem)
        ↓
Google Geocoding API (lat/lng da origem)
        ↓
CSV IBGE — filtra candidatos por linha reta (buffer 1.6x)
        ↓
Google Distance Matrix API — distância rodoviária real por lotes
        ↓
Filtro: road_km ≤ max_km  →  lista de municípios na rota
        ↓
CNES API — busca estabelecimentos por co_municipio (paginado)
        ↓
Scoring: tipo × leitos × gestão × distância (0–100 pts)
        ↓
Folium map + Streamlit dashboard + Export Excel/CSV
```

---

## Score de Potencial de Medicamentos de Alto Custo

| Fator                | Peso máximo |
|----------------------|-------------|
| Tipo de unidade      | 50 pts      |
| Leitos de internação | 30 pts      |
| Leitos SUS           | 10 pts      |
| Tipo de gestão       | 10 pts      |
| **Total**            | **100 pts** |

- **≥ 60** → Alto potencial (marcador vermelho)
- **35–59** → Médio potencial (marcador laranja)
- **< 35** → Baixo potencial (marcador cinza)

---

## Roadmap

### Fase 1 (atual) — MVP
- [x] Autocomplete de cidades brasileiras
- [x] Cálculo de distâncias rodoviárias via Distance Matrix
- [x] Consulta CNES paginada com cache
- [x] Mapa Folium com clusters por categoria
- [x] Rotas reais via Directions API
- [x] Score de potencial de alto custo
- [x] Export Excel + CSV

### Fase 2 — Enriquecimento
- [ ] Filtro por especialidade médica (oncologia, nefrologia, etc.)
- [ ] Dados de habilitações CNES (serviços especializados)
- [ ] Comparativo entre múltiplas origens
- [ ] Heatmap de densidade

### Fase 3 — Inteligência Comercial
- [ ] Histórico de buscas (SQLite / Supabase)
- [ ] CRM básico (anotações por estabelecimento)
- [ ] Alertas de novos estabelecimentos
- [ ] API própria para integração com outros sistemas

---

## Notas técnicas

- **CNES co_municipio**: usa os primeiros 6 dígitos do código IBGE de 7 dígitos
- **Cache**: municípios CSV = 24h; CNES por município = 1h; rotas = 1h
- **Rate limiting**: 80ms entre lotes Distance Matrix; 50ms entre páginas CNES
- **API Key usada**: chave "Google Maps API" (mais completa, com Distance Matrix, Routes, Places New)
