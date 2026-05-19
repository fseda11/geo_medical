"""
Configurações centrais da aplicação Health Route Intelligence
"""

# ── API Keys ──────────────────────────────────────────────────────────────────
# Chave "Google Maps API" (mais completa) — usada em produção
GOOGLE_API_KEY = "AIzaSyCeOcS2Cnz7p5iPhFw3SK2dtQ9aGgqSR3A"

# ── URLs de APIs externas ─────────────────────────────────────────────────────
GMAPS_GEOCODE_URL       = "https://maps.googleapis.com/maps/api/geocode/json"
GMAPS_DISTANCE_URL      = "https://maps.googleapis.com/maps/api/distancematrix/json"
GMAPS_DIRECTIONS_URL    = "https://maps.googleapis.com/maps/api/directions/json"
GMAPS_PLACES_AC_URL     = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
GMAPS_PLACES_DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"

CNES_BASE_URL           = "https://apidadosabertos.saude.gov.br/cnes"
MUNICIPALITIES_CSV_URL  = (
    "https://raw.githubusercontent.com/kelvins/municipios-brasileiros"
    "/main/csv/municipios.csv"
)

# ── Parâmetros de busca ───────────────────────────────────────────────────────
DEFAULT_DISTANCE_KM     = 150
STRAIGHT_LINE_FACTOR    = 1.6   # buffer straight-line para compensar desvios de estrada
ROUTE_SAMPLE_INTERVAL   = 10    # km entre pontos amostrados na rota visual
DISTANCE_MATRIX_BATCH   = 25    # máx destinos por chamada Distance Matrix
CNES_PAGE_SIZE          = 50    # registros por página CNES

# ── Classificação de unidades CNES ────────────────────────────────────────────
UNIT_TYPES = {
    1:  "POSTO DE SAÚDE",
    2:  "CENTRO DE SAÚDE / UBS",
    4:  "POLICLÍNICA",
    5:  "HOSPITAL GERAL",
    7:  "HOSPITAL ESPECIALIZADO",
    15: "UNIDADE MISTA",
    20: "PRONTO SOCORRO GERAL",
    21: "PRONTO SOCORRO ESPECIALIZADO",
    22: "CONSULTÓRIO ISOLADO",
    36: "CLÍNICA / CENTRO DE ESPECIALIDADE",
    39: "UPA",
    43: "FARMÁCIA",
    50: "HOSPITAL DIA",
    65: "PRONTO ATENDIMENTO",
    70: "CAPS",
    32: "SECRETARIA MUNICIPAL DE SAÚDE",
    33: "SECRETARIA ESTADUAL DE SAÚDE",
    68: "SECRETARIA DE SAÚDE",
}

# Tipos relevantes para medicamentos de alto custo
HIGH_COST_RELEVANT_TYPES = {5, 7, 15, 36, 39, 50, 20, 21, 65, 43}

# Mapeamento tipo → categoria visual
CATEGORY_MAP = {
    5:  "hospital",  7:  "hospital", 15: "hospital", 50: "hospital",
    20: "upa",       21: "upa",      39: "upa",       65: "upa",
    36: "clinica",   4:  "clinica",
    43: "farmacia",
    1:  "ubs",       2:  "ubs",      71: "ubs",
    32: "secretaria", 33: "secretaria", 68: "secretaria",
}

CATEGORY_COLORS = {
    "hospital": "#D32F2F",
    "upa":      "#F57C00",
    "clinica":  "#1976D2",
    "farmacia": "#388E3C",
    "ubs":      "#0288D1",
    "outro":    "#757575",
    "secretaria": "#6A1B9A",
}

CATEGORY_ICONS = {
    "hospital": "🏥",
    "upa":      "🚨",
    "clinica":  "🏨",
    "farmacia": "💊",
    "ubs":      "🩺",
    "outro":    "🏢",
    "secretaria": "🏛️",
}

# ── Scoring de potencial de medicamentos de alto custo ────────────────────────
SCORE_WEIGHTS = {
    "hospital":  50,
    "upa":       30,
    "clinica":   30,
    "farmacia":  40,
    "ubs":       10,
    "outro":     5,
    "secretaria": 20,
}