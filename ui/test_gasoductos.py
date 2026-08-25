"""Altas y bajas de gasoductos: el invariante es que el área inyecta lo mismo."""
import sys
import pandas as pd

sys.path.insert(0, ".")
from pipeline.gasoductos.intervenciones import (
    Intervencion, aplicar_intervenciones, areas_disponibles, volumen_area,
    destinos_area, gasoductos_disponibles)

COMPUESTOS = ["C1", "C2", "C3"]


def tablas():
    """Dos áreas. 'Chivo' reparte en 3 destinos, 'Alta' sólo tiene VMN."""
    yac = pd.DataFrame([
        {"Area": "Chivo", "HUB": "H1", "Gasoducto": "VMN", "Volumen_inyectado": 600.0,
         "C1": .85, "C2": .10, "C3": .05},
        {"Area": "Chivo", "HUB": "H1", "Gasoducto": "VMS", "Volumen_inyectado": 300.0,
         "C1": .85, "C2": .10, "C3": .05},
        {"Area": "Chivo", "HUB": "H1", "Gasoducto": "MEGA", "Volumen_inyectado": 100.0,
         "C1": .85, "C2": .10, "C3": .05},
        {"Area": "Alta", "HUB": "H2", "Gasoducto": "VMN", "Volumen_inyectado": 400.0,
         "C1": .88, "C2": .08, "C3": .04},
    ])
    fdi = pd.DataFrame([
        {"Area": "VMN", "HUB": "-", "Gasoducto": "TTY", "Volumen_inyectado": 1000.0,
         "C1": .86, "C2": .09, "C3": .05},
        {"Area": "VMS", "HUB": "-", "Gasoducto": "TTY", "Volumen_inyectado": 300.0,
         "C1": .85, "C2": .10, "C3": .05},
    ])
    matriz = pd.DataFrame({"TTY": ["VMN", "VMS"], "MEGA": ["Chivo", None]})
    return yac, fdi, matriz


# ===========================================================================
# Consultas
# ===========================================================================
yac, fdi, matriz = tablas()
assert areas_disponibles(yac) == ["Chivo", "Alta"], "ordenadas por volumen"
assert volumen_area(yac, "Chivo") == 1000.0
assert list(destinos_area(yac, "Chivo").index) == ["VMN", "VMS", "MEGA"]
assert gasoductos_disponibles(yac, fdi) == ["VMN", "VMS"], "MEGA es planta, no ducto"
print("OK consultas: áreas por volumen, ductos separados de plantas")


# ===========================================================================
# ALTA
# ===========================================================================
yac, fdi, matriz = tablas()
alta = Intervencion(tipo="alta", nombre="GNuevo", area_origen="Chivo",
                    planta_destino="TTY", volumen=250.0,
                    cromato=pd.Series({"C1": .80, "C2": .14, "C3": .06}))
y2, f2, m2, inf = aplicar_intervenciones(yac, fdi, [alta], COMPUESTOS, matriz)

assert not inf.errores, inf.errores
assert abs(volumen_area(y2, "Chivo") - 1000.0) < 1e-9, "el área inyecta lo mismo"
print("OK alta: el volumen del área no cambia (invariante)")

d = destinos_area(y2, "Chivo")
assert abs(d["GNuevo"] - 250.0) < 1e-9
# 750 restantes repartidos 600:300:100 -> 450:225:75
for destino, esperado in [("VMN", 450.0), ("VMS", 225.0), ("MEGA", 75.0)]:
    assert abs(d[destino] - esperado) < 1e-9, (destino, d[destino])
print("OK alta: el resto se reparte manteniendo la proporción original",
      {k: round(v, 1) for k, v in d.items()})

# la fila de flujos directos es la que hace que la planta lo vea
nueva_fd = f2[f2["Area"] == "GNuevo"]
assert len(nueva_fd) == 1 and nueva_fd.iloc[0]["Gasoducto"] == "TTY"
assert abs(float(nueva_fd.iloc[0]["Volumen_inyectado"]) - 250.0) < 1e-9
assert abs(float(nueva_fd.iloc[0]["C2"]) - 0.14) < 1e-12, "usa la croma cargada"
assert nueva_fd.iloc[0]["HUB"] == "-", "hereda el esquema de la plantilla"
print("OK alta: fila en flujos directos con la croma subida")

assert "GNuevo" in set(m2["TTY"].dropna()), "declarado en matriz_inyecciones"
print("OK alta: declarado como origen en matriz_inyecciones")

# el original no se toca
assert len(yac) == 4 and abs(destinos_area(yac, "Chivo")["VMN"] - 600.0) < 1e-9
print("OK alta: las tablas originales quedan intactas")

# volumen por encima del total -> se recorta y avisa
alta_grande = Intervencion(tipo="alta", nombre="GX", area_origen="Chivo",
                           planta_destino="TTY", volumen=5000.0)
y3, f3, _, inf3 = aplicar_intervenciones(yac, fdi, [alta_grande], COMPUESTOS, matriz)
assert any("recorta" in a for a in inf3.avisos), inf3.avisos
assert abs(destinos_area(y3, "Chivo")["GX"] - 1000.0) < 1e-9
assert abs(volumen_area(y3, "Chivo") - 1000.0) < 1e-9
otros = destinos_area(y3, "Chivo").drop("GX")
assert otros.sum() < 1e-9, "si se lleva todo, los demás quedan en cero"
assert len(otros) == 3, "pero las filas se conservan"
print("OK alta: recorta al tope del área y conserva las filas en cero")

# sin cromatografía -> hereda la del área
alta_sin = Intervencion(tipo="alta", nombre="GY", area_origen="Chivo",
                        planta_destino="TTY", volumen=100.0)
y4, _, _, _ = aplicar_intervenciones(yac, fdi, [alta_sin], COMPUESTOS, matriz)
fila = y4[y4["Gasoducto"] == "GY"].iloc[0]
assert abs(float(fila["C2"]) - 0.10) < 1e-12
print("OK alta sin croma: hereda la del área")

# errores
for interv, marca in [
    (Intervencion("alta", "GZ", area_origen="Inexistente", planta_destino="TTY", volumen=10), "no inyecta"),
    (Intervencion("alta", "VMN", area_origen="Chivo", planta_destino="TTY", volumen=10), "ya existe"),
    (Intervencion("alta", "GW", area_origen="Chivo", planta_destino="NoPlanta", volumen=10), "plantilla"),
]:
    _, _, _, i = aplicar_intervenciones(yac, fdi, [interv], COMPUESTOS, matriz)
    assert any(marca in e for e in i.errores), (marca, i.errores)
print("OK alta: área inexistente, nombre repetido y planta desconocida son errores")


# ===========================================================================
# BAJA
# ===========================================================================
yac, fdi, matriz = tablas()
baja = Intervencion(tipo="baja", nombre="VMS")
y5, f5, _, inf5 = aplicar_intervenciones(yac, fdi, [baja], COMPUESTOS, matriz)

assert not inf5.errores, inf5.errores
assert abs(volumen_area(y5, "Chivo") - 1000.0) < 1e-9
d5 = destinos_area(y5, "Chivo")
assert "VMS" not in d5.index
# los 300 de VMS se reparten entre VMN(600) y MEGA(100) -> 700:100 sobre 700
for destino, esperado in [("VMN", 600 * 1000 / 700), ("MEGA", 100 * 1000 / 700)]:
    assert abs(d5[destino] - esperado) < 1e-9, (destino, d5[destino])
assert "VMS" not in set(f5["Area"]), "también sale de flujos directos"
print("OK baja: el volumen se redistribuye proporcional",
      {k: round(v, 1) for k, v in d5.items()})

# área sin alternativa: se avisa y NO se redistribuye
baja_vmn = Intervencion(tipo="baja", nombre="VMN")
y6, _, _, inf6 = aplicar_intervenciones(yac, fdi, [baja_vmn], COMPUESTOS, matriz)
assert any("sin otro destino" in a or "no tienen" in a for a in inf6.avisos), inf6.avisos
assert abs(volumen_area(y6, "Chivo") - 1000.0) < 1e-9, "Chivo sí tiene alternativas"
assert volumen_area(y6, "Alta") == 0.0, "Alta pierde su única ruta"
print("OK baja: el área sin alternativa se reporta y su gas no se inventa")

# baja de algo que no existe
_, _, _, inf7 = aplicar_intervenciones(yac, fdi, [Intervencion("baja", "Fantasma")],
                                       COMPUESTOS, matriz)
assert any("ningún área" in a for a in inf7.avisos), inf7.avisos
print("OK baja de un ducto inexistente: avisa, no rompe")


# ===========================================================================
# Combinadas y round-trip
# ===========================================================================
yac, fdi, matriz = tablas()
combo = [Intervencion("baja", "VMS"),
         Intervencion("alta", "GNuevo", area_origen="Chivo",
                      planta_destino="TTY", volumen=200.0)]
y8, f8, _, inf8 = aplicar_intervenciones(yac, fdi, combo, COMPUESTOS, matriz)
assert not inf8.errores, inf8.errores
assert abs(volumen_area(y8, "Chivo") - 1000.0) < 1e-9
d8 = destinos_area(y8, "Chivo")
assert "VMS" not in d8.index and abs(d8["GNuevo"] - 200.0) < 1e-9
print("OK combinadas: baja primero, alta sobre lo que quedó",
      {k: round(v, 1) for k, v in d8.items()})

# desactivar una intervención la saca sin borrarla
combo[1].activa = False
y9, _, _, _ = aplicar_intervenciones(yac, fdi, combo, COMPUESTOS, matriz)
assert "GNuevo" not in destinos_area(y9, "Chivo").index
print("OK una intervención desactivada no se aplica")

d = alta.a_dict()
vuelta = Intervencion.desde_dict(d)
assert vuelta.nombre == "GNuevo" and abs(vuelta.volumen - 250.0) < 1e-9
assert abs(float(vuelta.cromato["C2"]) - 0.14) < 1e-12
print("OK round-trip JSON de la intervención")


# ===========================================================================
# CONVENCIONES MIXTAS — el caso del repo real
# ===========================================================================
# En el pipeline conviven dos formas del mismo nombre:
#   tabla_total_yacimientos['Gasoducto']    CRUDA        "VMN"
#   tabla_total_flujos_directos['Area']     NORMALIZADA  "vmn"
# porque el destino sale de los nombres de COLUMNA de la matriz (que no pasan
# por `normalizar`) y el origen de flujos directos sí pasa. Comparar los strings
# crudos daría intersección vacía.

def tablas_mixtas():
    yac = pd.DataFrame([
        {"Area": "chivo", "Gasoducto": "VMN", "Volumen_inyectado": 600.0,
         "C1": .85, "C2": .10, "C3": .05},
        {"Area": "chivo", "Gasoducto": "VMS", "Volumen_inyectado": 400.0,
         "C1": .85, "C2": .10, "C3": .05},
    ])
    fdi = pd.DataFrame([                       # normalizada, con acento perdido
        {"Area": "vmn", "Gasoducto": "TTY", "Volumen_inyectado": 600.0,
         "C1": .86, "C2": .09, "C3": .05},
        {"Area": "vms", "Gasoducto": "TTY", "Volumen_inyectado": 400.0,
         "C1": .85, "C2": .10, "C3": .05},
    ])
    matriz = pd.DataFrame({"TTY": ["VMN", "VMS"]})
    return yac, fdi, matriz


ym, fm, mm = tablas_mixtas()

assert gasoductos_disponibles(ym, fm) == ["VMN", "VMS"], gasoductos_disponibles(ym, fm)
print("OK convenciones mixtas: la intersección cruza 'VMN' con 'vmn'")

# ALTA: cada nombre tiene que quedar en la forma de SU columna
alta_mix = Intervencion("alta", "Gasoducto Ñuevo", area_origen="chivo",
                        planta_destino="TTY", volumen=300.0)
y, f, m, inf = aplicar_intervenciones(ym, fm, [alta_mix], COMPUESTOS, mm)
assert not inf.errores, inf.errores

destino_yac = y[y["Volumen_inyectado"] == 300.0]["Gasoducto"].iloc[0]
origen_fd = f[f["Gasoducto"] == "TTY"]["Area"].tolist()

assert destino_yac == "Gasoducto Ñuevo", f"yacimientos guarda la forma cruda: {destino_yac}"
assert "gasoductonuevo" in origen_fd, f"flujos directos guarda la normalizada: {origen_fd}"
print("OK alta: cruda en yacimientos, normalizada en flujos directos ->",
      repr(destino_yac), "/", repr("gasoductonuevo"))

# Y la matriz tiene que apuntar a lo mismo DESPUÉS de normalizar, porque
# `io_plantas` hace merge inner: si no coincide, la fila se descarta en silencio.
en_matriz = [x for x in m["TTY"].dropna()]
import unicodedata as _u
def _k(t):
    t = str(t).strip().lower()
    t = "".join(c for c in _u.normalize("NFD", t) if _u.category(c) != "Mn")
    return "".join(c for c in t if c.isalnum())
assert any(_k(x) == "gasoductonuevo" for x in en_matriz), en_matriz
assert set(f[f["Gasoducto"] == "TTY"]["Area"]) <= {_k(x) for x in en_matriz}, (
    "todo origen de flujos directos tiene que estar declarado en la matriz")
print("OK alta: la matriz normalizada cruza con flujos directos (merge inner)")

# BAJA con nombre en la forma cruda
y2, f2, _, inf2 = aplicar_intervenciones(ym, fm, [Intervencion("baja", "VMN")],
                                         COMPUESTOS, mm)
assert "VMN" not in set(y2["Gasoducto"]), "sale de yacimientos"
assert "vmn" not in set(f2["Area"]), "y también de flujos directos, pese a la otra forma"
assert abs(volumen_area(y2, "chivo") - 1000.0) < 1e-9
print("OK baja: limpia las dos tablas aunque el nombre esté en formas distintas")

print("\nTODO OK")


# ===========================================================================
# ESCENARIOS unificados: plantas + gasoductos en un archivo
# ===========================================================================
import json
from ui.escenarios import serializar, partir, resumen

interv_lista = [
    Intervencion("alta", "GNuevo", area_origen="Chivo", planta_destino="TTY",
                 volumen=250.0, cromato=pd.Series({"C1": .8, "C2": .15, "C3": .05})),
    Intervencion("baja", "VMS"),
]

texto = serializar({}, interv_lista)
plantas_json, ductos_json = partir(json.loads(texto))

assert plantas_json == []
assert len(ductos_json) == 2
assert ductos_json[0]["tipo"] == "alta" and ductos_json[1]["tipo"] == "baja"
print("OK escenario: los gasoductos se serializan junto a las plantas")

vuelta = [Intervencion.desde_dict(d) for d in ductos_json]
assert vuelta[0].nombre == "GNuevo" and abs(vuelta[0].volumen - 250.0) < 1e-9
assert abs(float(vuelta[0].cromato["C2"]) - 0.15) < 1e-12
assert vuelta[1].tipo == "baja"
print("OK escenario: round-trip completo con cromatografía")

# y las intervenciones recuperadas producen el mismo resultado
yac, fdi, matriz = tablas()
y_orig, _, _, _ = aplicar_intervenciones(yac, fdi, interv_lista, COMPUESTOS, matriz)
y_json, _, _, _ = aplicar_intervenciones(yac, fdi, vuelta, COMPUESTOS, matriz)
pd.testing.assert_frame_equal(
    y_orig.reset_index(drop=True), y_json.reset_index(drop=True))
print("OK escenario: las intervenciones recuperadas dan idéntico resultado")

assert "1 ducto(s) nuevo(s)" in resumen([], ductos_json)
assert "1 ducto(s) fuera de servicio" in resumen([], ductos_json)
print("OK resumen del escenario:", resumen([{"nombre": "X"}], ductos_json))

# formato viejo (lista de plantas) se sigue leyendo
p_viejo, g_viejo = partir([{"nombre": "MEGA"}])
assert len(p_viejo) == 1 and g_viejo == []
print("OK escenarios viejos (lista) siguen funcionando")

for malo in ("texto", 42, {"plantas": "no-lista"}):
    try:
        partir(malo)
    except ValueError:
        pass
    else:
        raise AssertionError(f"tendría que fallar: {malo}")
print("OK un escenario malformado falla explícito, no a medias")

print("\nTODO OK (escenarios)")
