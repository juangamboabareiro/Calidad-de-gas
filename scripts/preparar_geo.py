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
import re
import sys
import unicodedata
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

# Arriba de esto el GeoJSON pesa demasiado para mandarlo al navegador entero.
LIMITE_TRAMOS = 3000

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

def leer_geojson(ruta: Path, bbox=None) -> dict:
    """
    Lee un GeoJSON, o un shapefile si esta geopandas.

    Parameters
    ----------
    bbox : (oeste, sur, este, norte) | None
        Solo aplica a shapefiles. Se lo pasa a `read_file`, que usa el indice
        espacial de OGR para leer UNICAMENTE las features que caen adentro.
        Importa mucho: la capa de ductos Res. 319/93 tiene 179.201 tramos y
        6,8 millones de vertices; leerla entera son varios GB de RAM, y con
        bbox de la cuenca baja a una fraccion.
    """
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

    gdf = gpd.read_file(ruta, bbox=bbox) if bbox else gpd.read_file(ruta)

    return json.loads(gdf.to_crs(epsg=4326).to_json())


def parsear_filtros(texto: str | None) -> dict[str, set[str]]:
    """
    "TIPO=GAS,TIPO_TRAMO=TRONCAL|RAMAL" -> {"TIPO": {"GAS"}, ...}

    Varios valores para un mismo campo se separan con "|". La comparacion es
    sin distinguir mayusculas ni acentos, porque estos .dbf mezclan las dos
    cosas dentro de la misma columna.
    """
    if not texto:
        return {}

    filtros: dict[str, set[str]] = {}

    for parte in texto.split(","):
        if "=" not in parte:
            raise SystemExit(f"Filtro mal escrito: {parte!r}. Formato CAMPO=VALOR.")
        campo, valores = parte.split("=", 1)
        filtros[campo.strip()] = {
            _normalizar_clave(v) for v in valores.split("|") if v.strip()
        }

    return filtros


def filtrar_features(geojson: dict, filtros: dict, diametro_min=None) -> dict:
    """
    Se queda con las features que cumplen todos los filtros.

    `diametro_min` es aparte porque es numerico: sirve para sacar las lineas de
    captacion sin depender de como se llame el tipo de tramo en esta version de
    la capa. Un troncal de gas dificilmente baje de 8 pulgadas.
    """
    if not filtros and diametro_min is None:
        return geojson

    salidas = []

    for feat in geojson.get("features", []):
        props = feat.get("properties", {}) or {}

        if any(
            _normalizar_clave(props.get(campo, "")) not in valores
            for campo, valores in filtros.items()
        ):
            continue

        if diametro_min is not None:
            try:
                if float(props.get("DIAMETRO") or 0) < diametro_min:
                    continue
            except (TypeError, ValueError):
                continue

        salidas.append(feat)

    return {"type": "FeatureCollection", "features": salidas}


def _normalizar_clave(texto: str) -> str:
    """minusculas, sin acentos, sin separadores. NOMBRE_DE_ -> nombrede"""
    limpio = unicodedata.normalize("NFKD", str(texto))
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", limpio.lower())


def _nombre_de(props: dict) -> str | None:
    """
    Busca el nombre del area entre las propiedades de la feature.

    El .dbf de un shapefile trunca los encabezados a 10 caracteres, asi que el
    campo real puede llegar como NOMBRE_DE_ (por NOMBRE_DEL_AREA), NOM_AREA,
    DESCRIPCIO... Por eso no alcanza con una lista de nombres exactos: se
    compara normalizado y por PREFIJO, que cubre cualquier truncamiento.
    """
    normalizadas = {_normalizar_clave(k): k for k in props}

    # 1) coincidencia exacta con la lista, normalizada
    for clave in CLAVES_NOMBRE:
        real = normalizadas.get(_normalizar_clave(clave))
        if real and str(props[real]).strip() not in ("", "None", "nan"):
            return str(props[real]).strip()

    # 2) cualquier propiedad que empiece con "nombre", "area" o "yacimiento"
    for prefijo in ("nombre", "area", "yacimiento", "concesion"):
        for norm, real in normalizadas.items():
            if norm.startswith(prefijo) and str(props[real]).strip() not in ("", "None", "nan"):
                return str(props[real]).strip()

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


def _dist_perpendicular(p, a, b) -> float:
    """Distancia de p al segmento a-b, en grados escalados por cos(lat)."""
    k = math.cos(math.radians(a[1])) or 1.0

    px, py = p[0] * k, p[1]
    ax, ay = a[0] * k, a[1]
    bx, by = b[0] * k, b[1]

    dx, dy = bx - ax, by - ay
    largo2 = dx * dx + dy * dy

    if largo2 == 0:
        return math.hypot(px - ax, py - ay)

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / largo2))

    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _douglas_peucker(puntos: list, tolerancia: float) -> list:
    """
    Simplifica una polilinea conservando su forma.

    Iterativo y no recursivo: algunas trazas de la capa tienen miles de
    vertices y la version recursiva revienta el limite de stack de Python.
    """
    if len(puntos) < 3:
        return puntos

    conservar = [False] * len(puntos)
    conservar[0] = conservar[-1] = True

    pila = [(0, len(puntos) - 1)]

    while pila:
        ini, fin = pila.pop()

        peor, indice = 0.0, None

        for i in range(ini + 1, fin):
            d = _dist_perpendicular(puntos[i], puntos[ini], puntos[fin])
            if d > peor:
                peor, indice = d, i

        if indice is not None and peor > tolerancia:
            conservar[indice] = True
            pila.append((ini, indice))
            pila.append((indice, fin))

    return [p for p, keep in zip(puntos, conservar) if keep]


def simplificar(geojson: dict, tolerancia: float) -> tuple[dict, int, int]:
    """
    Aplica Douglas-Peucker a todas las lineas.

    La tolerancia va en grados: 0.001 son ~90 m a esta latitud. Para un mapa de
    cuenca eso es invisible, y en la capa de ductos suele recortar la mayoria de
    los vertices, que es de donde sale el peso del archivo.

    Returns
    -------
    (geojson, vertices_antes, vertices_despues)
    """
    if not tolerancia:
        return geojson, 0, 0

    antes = despues = 0
    salidas = []

    for feat in geojson.get("features", []):
        geom = feat.get("geometry") or {}
        tipo = geom.get("type")

        if tipo == "LineString":
            original = geom["coordinates"]
            nuevo = _douglas_peucker(original, tolerancia)
            antes += len(original)
            despues += len(nuevo)
            geom = {"type": tipo, "coordinates": nuevo}

        elif tipo == "MultiLineString":
            partes = []
            for linea in geom["coordinates"]:
                simple = _douglas_peucker(linea, tolerancia)
                antes += len(linea)
                despues += len(simple)
                partes.append(simple)
            geom = {"type": tipo, "coordinates": partes}

        salidas.append({**feat, "geometry": geom})

    return {"type": "FeatureCollection", "features": salidas}, antes, despues


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
    p.add_argument("--filtro-ductos", dest="filtro_ductos",
                   help='Filtros por atributo, ej: "TIPO=GAS,TIPO_TRAMO=TRONCAL|RAMAL". '
                        'Varios valores de un campo con "|".')
    p.add_argument("--diametro-min", dest="diametro_min", type=float,
                   help="Descarta tramos de menos de N pulgadas (saca captacion)")
    p.add_argument("--tolerancia", type=float, default=0.001,
                   help="Simplificacion de las trazas en grados. 0.001 = ~90 m, "
                        "invisible a escala de cuenca. 0 la desactiva.")
    args = p.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    DIR_GEO.mkdir(parents=True, exist_ok=True)

    print(f"Leyendo {args.concesiones}...")
    conces = compactar(leer_geojson(args.concesiones), ["empresa", "provincia"])
    conces = recortar(conces, bbox)
    print(f"  {len(conces['features'])} concesiones dentro del bbox")

    con_nombre = sum(1 for f in conces["features"] if f["properties"].get("nombre"))

    if conces["features"] and not con_nombre:
        disponibles = sorted(conces["features"][0]["properties"])
        raise SystemExit(
            "\nNinguna concesion trajo nombre: sin eso no se puede cruzar con el\n"
            "modelo y el CSV sale vacio de areas.\n"
            f"Propiedades disponibles en la primera feature: {disponibles}\n"
            "Agrega la que corresponda a CLAVES_NOMBRE, arriba en este archivo."
        )

    if con_nombre < len(conces["features"]):
        print(f"  OJO {len(conces['features']) - con_nombre} sin nombre, se descartan")

    destino = DIR_GEO / "concesiones.geojson"
    destino.write_text(json.dumps(conces, separators=(",", ":")), encoding="utf-8")
    print(f"  -> {destino} ({destino.stat().st_size / 1e6:.1f} MB)")

    if args.ductos:
        print(f"Leyendo {args.ductos}...")

        # bbox al leer, no despues: evita cargar los 179.201 tramos del pais.
        crudo = leer_geojson(args.ductos, bbox=bbox)
        print(f"  {len(crudo['features'])} tramos en el bbox")

        filtros = parsear_filtros(args.filtro_ductos)
        filtrado = filtrar_features(crudo, filtros, args.diametro_min)

        if filtros or args.diametro_min:
            print(f"  {len(filtrado['features'])} tras filtrar")

        if args.tolerancia:
            filtrado, antes, despues = simplificar(filtrado, args.tolerancia)
            if antes:
                print(f"  {antes:,} -> {despues:,} vertices "
                      f"({100 * (1 - despues / antes):.0f}% menos)")

        ductos = compactar(filtrado, ["TIPO", "TIPO_TRAMO", "DIAMETRO", "EMPRESA_IN"])

        if len(ductos["features"]) > LIMITE_TRAMOS:
            print(
                f"\n  OJO: {len(ductos['features'])} tramos es mucho para el navegador.\n"
                "  El GeoJSON viaja entero en cada render y la pestana se puede colgar.\n"
                "  Afinate el filtro: --filtro-ductos \"TIPO=GAS\" --diametro-min 8\n"
            )

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
