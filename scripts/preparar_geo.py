"""
Prepara la geodata local del mapa. Corre una sola vez, sin red.

Por que offline
---------------
La app no puede salir a internet (firewall de IT), asi que no hay WMS ni
basemap remoto: todo lo que el mapa dibuja tiene que estar en el repo. Este
script toma los archivos que se bajan UNA vez a mano y los deja en el formato
que consume `ui/mapa.py`.

Que hay que conseguir
---------------------
Dos archivos, en GeoJSON o shapefile:

1. Concesiones de explotacion (poligonos con el nombre del area).
       datos.energia.gob.ar  ->  dataset "Concesiones de explotacion"
2. Gasoductos / ductos de transporte (lineas).
       datos.energia.gob.ar  ->  dataset "Ductos de Transporte de Hidrocarburos"

Si esos portales tampoco se alcanzan, sirve igual cualquier export del GIS
interno: lo unico que se pide es que los poligonos tengan una propiedad con el
nombre del area.

Que produce
-----------
    datos/geo/concesiones.geojson   poligonos recortados y simplificados
    datos/geo/ductos.geojson        lineas
    datos/geo_nodos.csv             un punto por area (centroide) + plantas

Uso
---
    python scripts/preparar_geo.py --concesiones datos/crudo/concesiones.geojson \\
                                   --ductos      datos/crudo/ductos.geojson

Sin dependencias fuera de la stdlib si los archivos son GeoJSON. Para
shapefile hace falta geopandas, pero conviene evitarlo: se convierte una vez
en QGIS (clic derecho sobre la capa -> Exportar -> Guardar como -> GeoJSON,
CRS EPSG:4326) y despues el repo queda liviano y sin dependencias pesadas.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.normalizacion import canonizar_areas  # noqa: E402
from io_.loaders import ALIAS_AREAS  # noqa: E402

import pandas as pd  # noqa: E402


DIR_GEO = Path("datos") / "geo"
SALIDA_NODOS = Path("datos") / "geo_nodos.csv"

# Decimales que se conservan en las coordenadas. 4 son ~11 m: mas que suficiente
# para un mapa de cuenca, y achica el archivo a la mitad o menos.
DECIMALES = 4

# Candidatos de propiedad con el nombre del area. Varian entre versiones y
# entre organismos.
CLAVES_NOMBRE = [
    "nombre", "NOMBRE", "area", "AREA", "concesion", "CONCESION",
    "nom_area", "NOM_AREA", "descripcio", "DESCRIPCIO", "yacimiento",
]

# Nodos que no son concesiones: hay que cargarles lat/lon a mano una vez.
NODOS_MANUALES = [
    ("TTY", "planta"), ("MEGA", "planta"),
    ("TBX El Porton", "planta"), ("VM LIQ", "planta"),
    ("BdP", "gasoducto"), ("CO (Paralelo)", "gasoducto"),
    ("CO (Troncal)", "gasoducto"), ("GPA (a Chile)", "gasoducto"),
    ("GPA (a MEGA)", "gasoducto"), ("GPM", "gasoducto"),
    ("NEUI", "gasoducto"), ("NEUII", "gasoducto"), ("Otros", "gasoducto"),
    ("Pampa EM - BM", "gasoducto"), ("Pampa SCH", "gasoducto"),
    ("TOTAL - APE / ASR", "gasoducto"), ("VMN", "gasoducto"),
    ("VMS", "gasoducto"), ("YPF - RDM", "gasoducto"),
]


# ===========================================================================
# Lectura
# ===========================================================================

def leer_geojson(ruta: Path) -> dict:
    """Lee un GeoJSON, o convierte un shapefile si hace falta geopandas."""
    ruta = Path(ruta)

    if ruta.suffix.lower() in (".geojson", ".json"):
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)

    try:
        import geopandas as gpd
    except ImportError:
        raise SystemExit(
            f"{ruta} no es GeoJSON y no esta geopandas instalado.\n"
            "Convertilo una vez en QGIS: clic derecho en la capa -> Exportar -> "
            "Guardar como -> GeoJSON, CRS EPSG:4326."
        )

    return json.loads(gpd.read_file(ruta).to_crs(epsg=4326).to_json())


def _nombre_de(props: dict) -> str | None:
    for clave in CLAVES_NOMBRE:
        valor = props.get(clave)
        if valor and str(valor).strip():
            return str(valor).strip()
    return None


# ===========================================================================
# Geometria (stdlib, sin shapely)
# ===========================================================================

def _anillos(geom: dict) -> list[list]:
    """Anillos exteriores de un Polygon o MultiPolygon."""
    tipo = geom.get("type")

    if tipo == "Polygon":
        return [geom["coordinates"][0]]
    if tipo == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]

    return []


def centroide(geom: dict) -> tuple[float, float] | None:
    """
    Centroide ponderado por area de un poligono, en lat/lon.

    La longitud se escala por cos(lat) antes del calculo y se desescala
    despues. Sin eso, a 38 grados sur un grado de longitud "pesa" un 21% de mas
    y el centroide de una concesion alargada en sentido este-oeste queda
    corrido varios kilometros.

    Usa la formula del poligono (shoelace). Si el area da cero (poligono
    degenerado) cae al promedio simple de los vertices.
    """
    anillos = _anillos(geom)

    if not anillos:
        return None

    todos = [pt for anillo in anillos for pt in anillo]
    lat_media = sum(p[1] for p in todos) / len(todos)
    k = math.cos(math.radians(lat_media)) or 1.0

    sx = sy = area2 = 0.0

    for anillo in anillos:
        for (x0, y0), (x1, y1) in zip(anillo, anillo[1:]):
            a = (x0 * k) * y1 - (x1 * k) * y0
            area2 += a
            sx += ((x0 * k) + (x1 * k)) * a
            sy += (y0 + y1) * a

    if abs(area2) < 1e-12:
        return (
            sum(p[0] for p in todos) / len(todos),
            sum(p[1] for p in todos) / len(todos),
        )

    return (sx / (3.0 * area2) / k, sy / (3.0 * area2))


def _redondear(coords):
    """Recorta decimales recursivamente en cualquier geometria."""
    if isinstance(coords, (int, float)):
        return round(float(coords), DECIMALES)
    return [_redondear(c) for c in coords]


def compactar(geojson: dict, propiedades: list[str]) -> dict:
    """
    Deja solo las propiedades pedidas y recorta decimales.

    Los shapefiles oficiales traen 20 o 30 campos por feature (expediente,
    decreto, fecha de vencimiento...). Nada de eso se usa y todo eso viaja al
    navegador en cada render.
    """
    salidas = []

    for feat in geojson.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue

        props = feat.get("properties", {}) or {}
        nombre = _nombre_de(props)

        salidas.append({
            "type": "Feature",
            "properties": {
                **{p: props.get(p) for p in propiedades if p in props},
                "nombre": nombre or "",
            },
            "geometry": {
                "type": geom["type"],
                "coordinates": _redondear(geom["coordinates"]),
            },
        })

    return {"type": "FeatureCollection", "features": salidas}


def recortar(geojson: dict, bbox: tuple[float, float, float, float]) -> dict:
    """Se queda con las features que tocan el bbox (oeste, sur, este, norte)."""
    oeste, sur, este, norte = bbox
    salidas = []

    for feat in geojson.get("features", []):
        planos = []

        def _juntar(c):
            if isinstance(c, (int, float)):
                return
            if len(c) == 2 and all(isinstance(v, (int, float)) for v in c):
                planos.append(c)
                return
            for sub in c:
                _juntar(sub)

        _juntar(feat.get("geometry", {}).get("coordinates", []))

        if not planos:
            continue

        xs = [p[0] for p in planos]
        ys = [p[1] for p in planos]

        if max(xs) < oeste or min(xs) > este or max(ys) < sur or min(ys) > norte:
            continue

        salidas.append(feat)

    return {"type": "FeatureCollection", "features": salidas}


# ===========================================================================
# Nodos
# ===========================================================================

def nodos_desde_concesiones(concesiones: dict) -> pd.DataFrame:
    filas = []

    for feat in concesiones["features"]:
        nombre = feat["properties"].get("nombre")
        if not nombre:
            continue

        centro = centroide(feat["geometry"])
        if centro is None:
            continue

        filas.append({
            "nombre": nombre,
            "tipo": "area",
            "lat": round(centro[1], 5),
            "lon": round(centro[0], 5),
            "fuente": "centroide concesion",
            "notas": "",
        })

    return pd.DataFrame(filas)


def combinar_nodos(nuevos: pd.DataFrame, salida: Path) -> pd.DataFrame:
    """
    Suma los centroides a lo que ya haya, SIN pisar cargas manuales.

    Si una fila ya tiene lat/lon, se respeta: el centroide de una concesion muy
    irregular puede caer fuera del area util, y quien lo corrigio sabe mas que
    este script. Por eso re-correr es seguro.
    """
    manuales = pd.DataFrame(NODOS_MANUALES, columns=["nombre", "tipo"])
    manuales["lat"] = pd.NA
    manuales["lon"] = pd.NA
    manuales["fuente"] = ""
    manuales["notas"] = "cargar a mano: no es una concesion"

    base = pd.concat([nuevos, manuales], ignore_index=True)
    base["clave"] = canonizar_areas(base["nombre"], ALIAS_AREAS)

    if salida.exists():
        previo = pd.read_csv(salida, comment="#")
        previo["clave"] = canonizar_areas(previo["nombre"], ALIAS_AREAS)

        cargadas = previo.dropna(subset=["lat", "lon"])
        base = base[~base["clave"].isin(set(cargadas["clave"]))]
        base = pd.concat([cargadas, base], ignore_index=True)

    base = base.drop_duplicates("clave", keep="first").drop(columns="clave")

    return base.sort_values(["tipo", "nombre"]).reset_index(drop=True)


# ===========================================================================
# Main
# ===========================================================================

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--concesiones", required=True, help="GeoJSON o SHP de concesiones")
    p.add_argument("--ductos", help="GeoJSON o SHP de gasoductos (opcional)")
    p.add_argument("--bbox", default="-71.5,-40.5,-66.5,-35.0",
                   help="oeste,sur,este,norte. Default: cuenca Neuquina")
    args = p.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    DIR_GEO.mkdir(parents=True, exist_ok=True)

    print(f"Leyendo {args.concesiones}...")
    conces = compactar(leer_geojson(args.concesiones), ["empresa", "provincia"])
    conces = recortar(conces, bbox)
    print(f"  {len(conces['features'])} concesiones dentro del bbox")

    destino = DIR_GEO / "concesiones.geojson"
    destino.write_text(json.dumps(conces, separators=(",", ":")), encoding="utf-8")
    print(f"  -> {destino} ({destino.stat().st_size / 1e6:.1f} MB)")

    if args.ductos:
        print(f"Leyendo {args.ductos}...")
        ductos = recortar(compactar(leer_geojson(args.ductos), ["empresa"]), bbox)
        print(f"  {len(ductos['features'])} tramos dentro del bbox")

        destino = DIR_GEO / "ductos.geojson"
        destino.write_text(json.dumps(ductos, separators=(",", ":")), encoding="utf-8")
        print(f"  -> {destino} ({destino.stat().st_size / 1e6:.1f} MB)")

    tabla = combinar_nodos(nodos_desde_concesiones(conces), SALIDA_NODOS)

    with open(SALIDA_NODOS, "w", encoding="utf-8", newline="") as f:
        f.write("# Coordenadas de areas, gasoductos y plantas para el mapa.\n")
        f.write("# Areas: centroide de la concesion. Plantas y gasoductos: a mano.\n")
        f.write("# Las filas con lat/lon ya cargada NO se pisan al re-correr.\n")
        tabla.to_csv(f, index=False, quoting=csv.QUOTE_MINIMAL)

    sin = int(tabla["lat"].isna().sum())
    print(f"\n-> {SALIDA_NODOS}: {len(tabla)} nodos, {sin} sin coordenadas.")

    if sin:
        print("\nFaltan (cargar lat/lon a mano):")
        for nombre in tabla.loc[tabla["lat"].isna(), "nombre"]:
            print(f"  {nombre}")


if __name__ == "__main__":
    main()
