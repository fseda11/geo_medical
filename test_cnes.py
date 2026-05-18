"""
test_cnes3.py — Descobre qual parâmetro a API CNES realmente aceita para filtrar município.
Execute: python test_cnes3.py
"""
import requests, json

BASE = "https://apidadosabertos.saude.gov.br/cnes/estabelecimentos"

def fetch(params, label):
    r = requests.get(BASE, params={**params, "limit": 5}, timeout=15)
    data = r.json()
    items = data.get("estabelecimentos", [])
    cnes_ids = [str(i.get("codigo_cnes","?")) for i in items]
    first_muni = items[0].get("codigo_municipio","?") if items else "—"
    print(f"\n[{label}]  HTTP {r.status_code}  |  {len(items)} itens  |  1º muni={first_muni}")
    print(f"  CÓD. CNES retornados: {cnes_ids}")

print("="*60)
print("TESTE 1 — parâmetros de município diferentes")
print("="*60)

# Petrópolis (grande) vs Paraíba do Sul (pequena)
fetch({"co_municipio":   "330390"}, "Petrópolis co_municipio=330390")
fetch({"co_municipio":   "330350"}, "Paraíba do Sul co_municipio=330350")

print("\n" + "="*60)
print("TESTE 2 — outros formatos de código")
print("="*60)

fetch({"co_municipio": "3303906"}, "Petrópolis IBGE completo 3303906")
fetch({"co_municipio": "3303500"}, "Paraíba do Sul IBGE completo 3303500")
fetch({"codigo_municipio": "330390"}, "Petrópolis codigo_municipio=330390")
fetch({"municipio":      "330390"}, "Petrópolis municipio=330390")

print("\n" + "="*60)
print("TESTE 3 — por nome do município")
print("="*60)

fetch({"ds_municipio": "PETROPOLIS"},    "ds_municipio=PETROPOLIS")
fetch({"ds_municipio": "PARAIBA DO SUL"},"ds_municipio=PARAIBA DO SUL")
fetch({"no_municipio": "PETROPOLIS"},    "no_municipio=PETROPOLIS")

print("\n" + "="*60)
print("TESTE 4 — verifica se resultados são diferentes entre municípios")
print("="*60)

r1 = requests.get(BASE, params={"co_municipio":"330390","limit":5}, timeout=15)
r2 = requests.get(BASE, params={"co_municipio":"330350","limit":5}, timeout=15)
ids1 = {i["codigo_cnes"] for i in r1.json().get("estabelecimentos",[])}
ids2 = {i["codigo_cnes"] for i in r2.json().get("estabelecimentos",[])}
print(f"IDs Petrópolis:      {ids1}")
print(f"IDs Paraíba do Sul:  {ids2}")
print(f"São idênticos?       {ids1 == ids2}  ← se True, o filtro está quebrado")

print("\n" + "="*60)
print("TESTE 5 — sem filtro (baseline global)")
print("="*60)
fetch({}, "sem filtro (primeiros 5 do mundo)")