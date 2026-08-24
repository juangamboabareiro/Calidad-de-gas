"""
Genera `datos/geo_nodos.csv` a partir del shapefile oficial de concesiones.

Por que asi y no a mano
-----------------------
La capa `planosbase_concesiones_explotacion` de la Secretaria de Energia trae
los poligonos de concesion CON el nombre del area. El centroide de cada poligono
es la coordenada que necesita el mapa, y el nombre se puede cruzar contra el
modelo con la misma `canonizar_areas` + `alias_areas.csv` que usa el pipeline.

O sea: las ~130 areas salen solas. Lo unico que queda a mano son las plantas y
los gasoductos, que no son concesiones y por lo tanto no estan en esa capa
(~19 filas).

De donde bajar el shapefile
---------------------------
    http://datos.energia.gob.ar/dataset/concesiones-de-explotacion
    (buscar "Concesiones de explotacion", formato SHP)

Alternativa sin descargar nada: el mismo servicio WMS expone WFS, asi que
geopandas puede leerlo directo por URL. Es mas lento pero no deja archivos.

Uso
---
    python scripts/geo_desde_concesiones.py datos/concesiones.shp
    python scripts/geo_desde_concesiones.py --wfs

Deja el CSV en `datos/geo_nodos.csv` sin pisar lo que ya este cargado a mano:
las filas existentes con lat/lon se respetan.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.normalizacion import canonizar_areas  # noqa: E402
from io_.loaders import ALIAS_AREAS  # noqa: E402


SALIDA = Path("datos") / "geo_nodos.csv"

WFS_CONCESIONES = (
    "https://sig.se.gob.ar/wmsenergia?service=WFS&version=1.0.0"
    "&request=GetFeature&typeName=planosbase_concesiones_explotacion"
    "&outputFormat=application/json"
)

# Candidatos de nombre de columna en el shapefile. Varian entre versiones.
COLUMNAS_NOMBRE = ["nombre", "NOMBRE", "area", "AREA", "concesion", "CONCESION",
                   "nom_area", "NOM_AREA", "descripcio", "DESCRIPCIO"]

# Nodos que NO son concesiones y hay que cargar a mano.
NODOS_MANUALES = [
    ("TTY", "planta"),
    ("MEGA", "planta"),
    ("TBX El Porton", "planta"),
    ("VM LIQ", "planta"),
    ("BdP", "gasoducto"),
    ("CO (Paralelo)", "gasoducto"),
    ("CO (Troncal)", "gasoducto"),
    ("GPA (a Chile)", "gasoducto"),
    ("GPA (a MEGA)", "gasoducto"),
    ("GPM", "gasoducto"),
    ("NEUI", "gasoducto"),
    ("NEUII", "gasoducto"),
    ("Otros", "gasoducto"),
    ("Pampa EM - BM", "gasoducto"),
    ("Pampa SCH", "gasoducto"),
    ("TOTAL - APE / ASR", "gasoducto"),
    ("VMN", "gasoducto"),
    ("VMS", "gasoducto"),
    ("YPF - RDM", "gasoducto"),
]


def _columna_nombre(gdf) -> str:
    for c in COLUMNAS_NOMBRE:
        if c in gdf.columns:
            return c

    raise KeyError(
        f"No encuentro la columna de nombre. Columnas disponibles: "
        f"{sorted(gdf.columns)}. Agregala a COLUMNAS_NOMBRE."
    )


def centroides_concesiones(origen: str) -> pd.DataFrame:
    """
    Lee el shapefile (o el WFS) y devuelve nombre + centroide en lat/lon.

    Los centroides se calculan en una proyeccion metrica (POSGAR 2007 / faja 2,
    EPSG:5345) y recien despues se pasan a lat/lon: calcularlos directo en
    grados da un punto corrido, mas notorio cuanto mas alargado el poligono.
    """
    import geopandas as gpd

    gdf = gpd.read_file(origen)

    if gdf.crs is None:
        raise ValueError("El archivo no declara CRS; abrilo en QGIS y asignalo.")

    col = _columna_nombre(gdf)

    metrico = gdf.to_crs(epsg=5345)
    centros = metrico.geometry.centroid.to_crs(epsg=4326)

    return pd.DataFrame({
        "nombre": gdf[col].astype(str).str.strip(),
        "tipo": "area",
        "lat": centros.y.values,
        "lon": centros.x.values,
        "fuente": "centroide concesion (SE)",
        "notas": "",
    })


def combinar(nuevos: pd.DataFrame, salida: Path) -> pd.DataFrame:
    """
    Suma los centroides a lo que ya haya, sin pisar cargas manuales.

    Regla: si una fila ya tiene lat/lon, se respeta. Es a proposito: el
    centroide de una concesion muy irregular puede caer fuera del area util, y
    quien lo corrigio a mano sabe mas que este script.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shapefile", nargs="?", help="Ruta al .shp de concesiones")
    parser.add_argument("--wfs", action="store_true",
                        help="Leer del WFS de la Secretaria en vez de un archivo")
    parser.add_argument("-o", "--salida", default=str(SALIDA))
    args = parser.parse_args()

    if not args.shapefile and not args.wfs:
        parser.error("Pasa un shapefile o usa --wfs")

    origen = WFS_CONCESIONES if args.wfs else args.shapefile

    print(f"Leyendo {origen}...")
    nuevos = centroides_concesiones(origen)
    print(f"  {len(nuevos)} concesiones con centroide")

    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)

    tabla = combinar(nuevos, salida)

    with open(salida, "w", encoding="utf-8") as f:
        f.write("# Coordenadas de areas, gasoductos y plantas para el mapa.\n")
        f.write("# Areas: centroide de la concesion (capa oficial de la SE).\n")
        f.write("# Plantas y gasoductos: cargar a mano, no son concesiones.\n")
        f.write("# Las filas con lat/lon ya cargada NO se pisan al re-correr.\n")
        tabla.to_csv(f, index=False)

    sin_coord = tabla["lat"].isna().sum()
    print(f"Escrito {salida}: {len(tabla)} nodos, {sin_coord} sin coordenadas.")

    if sin_coord:
        print("\nFaltan (cargar a mano):")
        for nombre in tabla.loc[tabla["lat"].isna(), "nombre"]:
            print(f"  {nombre}")


if __name__ == "__main__":
    main()
