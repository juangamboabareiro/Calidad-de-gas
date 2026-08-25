"""
Mapa de la red: areas, gasoductos y plantas sobre el territorio real.

Que reemplaza
-------------
El tab "Red de Gasoductos" dibujaba un grafo de graphviz con ~140 aristas
Area -> Gasoducto. Sin geografia y con esa cantidad de nodos queda ilegible, y
ademas no dice nada que la tabla no diga mejor.

TODO LOCAL
----------
La app no tiene salida a internet (firewall de IT). No hay WMS, no hay basemap
de Carto, no hay tiles. `map_style=None` deja el lienzo vacio y el contexto
geografico lo ponen dos GeoJSON versionados en el repo:

    datos/geo/concesiones.geojson   poligonos de concesion (el "fondo")
    datos/geo/ductos.geojson        trazas de gasoductos
    datos/geo_nodos.csv             un punto por area / gasoducto / planta

Se generan una sola vez con `scripts/preparar_geo.py`. Ver ese archivo para de
donde bajar los originales.

Sale mejor que con WMS, de paso: es vectorial, se puede estilar, responde al
hover y no depende de que un servicio externo este arriba.

Degradado
---------
Sin pydeck avisa como instalarlo. Sin GeoJSON dibuja igual los nodos y los
flujos, solo que sin fondo. Sin coordenadas explica que generar. Nunca deja el
tab en blanco.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from ui.compat import ancho, arrow_safe

try:
    import pydeck as pdk
except ImportError:  # pragma: no cover
    pdk = None

from pipeline.cromatografia import clave_cruce


# Anclado a la raiz del repo, no al directorio desde donde se lanza Streamlit.
# Con rutas relativas, `streamlit run app.py` parado en otra carpeta no encuentra
# nada y el tab queda pidiendo un archivo que en realidad existe.
RAIZ = Path(__file__).resolve().parent.parent

DIR_GEO = RAIZ / "datos" / "geo"
RUTA_NODOS = RAIZ / "datos" / "geo_nodos.csv"
RUTA_CONCESIONES = DIR_GEO / "concesiones.geojson"
RUTA_DUCTOS = DIR_GEO / "ductos.geojson"

COLOR_TIPO = {
    "planta": [200, 30, 40, 230],
    "gasoducto": [30, 90, 160, 210],
    "area": [90, 160, 90, 190],
}

RADIO_TIPO = {"planta": 4500, "gasoducto": 2800, "area": 1500}

# Paleta para el diametro, de menor a mayor. Saturada a proposito: el fondo de
# concesiones ya es gris, y una paleta apagada encima se pierde.
PALETA_DIAMETRO = [
    [  0, 170, 200, 230],   # turquesa
    [ 60, 140, 255, 235],   # azul
    [255, 150,   0, 240],   # naranja
    [225,  30,  60, 245],   # rojo
]

# Grosor en pixeles por tramo, del mas fino al mas grueso.
ANCHOS_DIAMETRO = [1.2, 2.0, 3.2, 4.6]

# Fuera de este rango es centinela ("sin dato"): la capa trae valores de 9999.
DIAMETRO_MIN_VALIDO = 0.5
DIAMETRO_MAX_VALIDO = 60.0

# Paleta categorica para colorear por empresa. Se cicla si hay mas empresas.
PALETA_EMPRESAS = [
    [ 31, 119, 180, 230], [255, 127,  14, 230], [ 44, 160,  44, 230],
    [214,  39,  40, 230], [148, 103, 189, 230], [140,  86,  75, 230],
    [227, 119, 194, 230], [ 90,  90,  95, 230], [188, 189,  34, 230],
    [ 23, 190, 207, 230],
]

COLOR_SIN_DATO = [170, 170, 175, 150]

# Gris de la traza cuando NO se agrupa. Ver `agrupar_ductos`: agrupar significa
# una capa de deck.gl por grupo, y por empresa eso son docenas. Sin agrupar es
# una sola capa con un color constante, que es el caso barato.
COLOR_TRAZA_NEUTRA = [120, 128, 138, 170]

MODO_SIN_COLOR = "Nada (gris)"

# Donde el tab de sandbox deja su red modificada. Si no corriste el sandbox la
# clave no existe y el mapa se comporta exactamente como antes.
CLAVE_RED_SANDBOX = "sandbox_red_gasoductos"


# ===========================================================================
# Carga
# ===========================================================================

def _firma(ruta) -> tuple[str, float]:
    """
    Ruta + fecha de modificacion, para usar como clave de cache.

    Sin esto, `st.cache_data` se queda con el resultado de la PRIMERA llamada.
    Si alguien abre el tab antes de generar la geodata, queda cacheado el
    DataFrame vacio y el mapa sigue diciendo que falta el archivo aunque ya
    exista. Un archivo que todavia no existe firma con mtime 0, asi que en
    cuanto se crea la firma cambia y el cache se invalida solo.
    """
    ruta = Path(ruta)
    return (str(ruta), ruta.stat().st_mtime if ruta.exists() else 0.0)


@st.cache_data(show_spinner=False)
def _leer_nodos(firma: tuple[str, float]) -> pd.DataFrame:
    ruta = Path(firma[0])

    if not ruta.exists():
        return pd.DataFrame(columns=["nombre", "tipo", "lat", "lon", "clave"])

    nodos = pd.read_csv(ruta, comment="#")

    for col in ("lat", "lon"):
        nodos[col] = pd.to_numeric(nodos.get(col), errors="coerce")

    nodos["tipo"] = nodos.get("tipo", "area").fillna("area").str.strip().str.lower()
    nodos["clave"] = clave_cruce(nodos["nombre"])

    return nodos


def cargar_nodos(ruta=RUTA_NODOS) -> pd.DataFrame:
    """Lee geo_nodos.csv. Vacio si todavia no existe."""
    return _leer_nodos(_firma(ruta))


@st.cache_data(show_spinner=False)
def _leer_geojson(firma: tuple[str, float]) -> dict | None:
    ruta = Path(firma[0])

    if not ruta.exists():
        return None

    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def cargar_geojson(ruta) -> dict | None:
    """Lee un GeoJSON local. None si no esta."""
    return _leer_geojson(_firma(ruta))


def _bbox(puntos: pd.DataFrame, margen: float = 0.4):
    return (
        float(puntos["lon"].min()) - margen,
        float(puntos["lat"].min()) - margen,
        float(puntos["lon"].max()) + margen,
        float(puntos["lat"].max()) + margen,
    )


# ===========================================================================
# Posicion derivada de los gasoductos
# ===========================================================================

def completar_gasoductos(nodos: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """
    Ubica los gasoductos sin coordenadas en el baricentro de lo que les inyecta.

    Un gasoducto es una LINEA, no un punto, asi que no tiene una coordenada
    "verdadera" que cargar. Y mientras no la tenga, ninguna arista se dibuja:
    los flujos van area -> gasoducto y hacen falta las dos puntas.

    Se lo ubica entonces en el centroide de las areas que le inyectan,
    ponderado por volumen. Es una posicion ESQUEMATICA: no es donde pasa el
    ducto, es desde donde le llega el gas. Sirve para que los arcos converjan
    en un lugar con sentido; para la traza real estan las lineas del GeoJSON.

    Cualquier gasoducto que ya tenga lat/lon cargada a mano se respeta: esta
    funcion solo rellena huecos.

    Returns
    -------
    pandas.DataFrame
        `nodos` con las coordenadas completadas y la columna `derivada` en True
        para los que se calcularon aca.
    """
    salida = nodos.copy()

    if "derivada" not in salida.columns:
        salida["derivada"] = False

    coords = {
        fila.clave: (float(fila.lon), float(fila.lat))
        for fila in salida.dropna(subset=["lat", "lon"]).itertuples()
    }

    faltan = salida[
        (salida["tipo"] == "gasoducto") & salida["lat"].isna()
    ]

    if faltan.empty or edges is None or not len(edges):
        return salida

    aristas = edges.copy()
    aristas["k_origen"] = clave_cruce(aristas["origen"])
    aristas["k_destino"] = clave_cruce(aristas["destino"])
    aristas["valor"] = pd.to_numeric(aristas["valor"], errors="coerce").fillna(0)

    for idx, nodo in faltan.iterrows():
        entrantes = aristas[
            (aristas["k_destino"] == nodo["clave"]) & (aristas["valor"] > 0)
        ]
        entrantes = entrantes[entrantes["k_origen"].isin(coords)]

        if entrantes.empty:
            continue

        peso = entrantes["valor"].sum()

        if peso <= 0:
            continue

        lon = sum(coords[k][0] * v for k, v in
                  zip(entrantes["k_origen"], entrantes["valor"])) / peso
        lat = sum(coords[k][1] * v for k, v in
                  zip(entrantes["k_origen"], entrantes["valor"])) / peso

        salida.loc[idx, ["lat", "lon", "derivada"]] = [lat, lon, True]

    return salida


# ===========================================================================
# Coloreo de las trazas
# ===========================================================================

def _diametros_validos(geojson: dict) -> list[float]:
    valores = []

    for feat in geojson.get("features", []):
        try:
            d = float((feat.get("properties") or {}).get("DIAMETRO"))
        except (TypeError, ValueError):
            continue
        if DIAMETRO_MIN_VALIDO <= d <= DIAMETRO_MAX_VALIDO:
            valores.append(d)

    return sorted(valores)


def cortes_diametro(geojson: dict, n: int = 4) -> list[float]:
    """
    Cortes por cuantiles de los diametros presentes, no una escala fija.

    Una escala fija falla en las dos direcciones: si se filtro por
    --diametro-min 10, todo cae en el tramo mas bajo y el mapa sale de un solo
    color; si no se filtro nada, todo cae en el mas bajo tambien porque la
    mediana de la capa es 4 pulgadas. Con cuantiles los colores siempre se
    reparten, sea cual sea el recorte.
    """
    valores = _diametros_validos(geojson)

    if len(valores) < n:
        return []

    cortes = []
    for k in range(1, n):
        corte = valores[int(len(valores) * k / n)]
        if corte not in cortes:
            cortes.append(corte)

    return cortes


def _fmt_pulgadas(valor: float) -> str:
    return f"{valor:g}″"


def agrupar_ductos(geojson: dict, modo: str) -> list[dict]:
    """
    Parte las trazas en grupos, cada uno con su color y grosor.

    Se devuelve un GeoJSON por grupo, en vez de uno solo con el color en las
    propiedades, para no depender de que pydeck resuelva un accesor anidado
    (`properties.color`). Si ese accesor no se interpreta, deck.gl cae al color
    por defecto y sale todo del mismo tono, que es exactamente el sintoma que
    queremos evitar. Con una capa por grupo el color es una constante.

    No muta el original: `cargar_geojson` esta cacheado. Se comparten las
    features tal cual, que son de solo lectura.

    Returns
    -------
    list[dict]
        Cada uno con etiqueta, color, ancho y features.
    """
    feats = geojson.get("features", [])

    if modo == MODO_SIN_COLOR:
        # UNA capa con todas las trazas y un color constante. Es la version
        # liviana: agrupar por empresa puede dar 20-30 capas de deck.gl, cada
        # una con su GeoJSON serializado aparte, y ahi es donde el mapa se
        # vuelve pesado. La geometria es la misma; lo que cambia es en cuantos
        # payloads se manda.
        return [{
            "etiqueta": f"trazas ({len(feats)})",
            "color": COLOR_TRAZA_NEUTRA,
            "ancho": 1.4,
            "features": feats,
        }]

    if modo == "Empresa":
        grupos: dict[str, list] = {}

        for f in feats:
            clave = str((f.get("properties") or {}).get("EMPRESA_IN") or "Sin dato")
            grupos.setdefault(clave, []).append(f)

        # Las empresas con mas tramos primero: asi los colores mas distinguibles
        # de la paleta caen en lo que mas se ve.
        orden = sorted(grupos, key=lambda k: -len(grupos[k]))

        return [
            {
                "etiqueta": f"{clave} ({len(grupos[clave])})",
                "color": PALETA_EMPRESAS[i % len(PALETA_EMPRESAS)],
                "ancho": 1.8,
                "features": grupos[clave],
            }
            for i, clave in enumerate(orden)
        ]

    # --- por diametro ------------------------------------------------------
    cortes = cortes_diametro(geojson, n=len(PALETA_DIAMETRO))

    if not cortes:
        return [{
            "etiqueta": "trazas",
            "color": PALETA_DIAMETRO[1],
            "ancho": 2.0,
            "features": feats,
        }]

    baldes = [[] for _ in range(len(cortes) + 1)]
    sin_dato = []

    for f in feats:
        try:
            d = float((f.get("properties") or {}).get("DIAMETRO"))
        except (TypeError, ValueError):
            sin_dato.append(f)
            continue

        if not (DIAMETRO_MIN_VALIDO <= d <= DIAMETRO_MAX_VALIDO):
            sin_dato.append(f)
            continue

        indice = sum(1 for c in cortes if d >= c)
        baldes[indice].append(f)

    etiquetas = []
    for i in range(len(baldes)):
        if i == 0:
            etiquetas.append(f"< {_fmt_pulgadas(cortes[0])}")
        elif i == len(baldes) - 1:
            etiquetas.append(f"≥ {_fmt_pulgadas(cortes[-1])}")
        else:
            etiquetas.append(
                f"{_fmt_pulgadas(cortes[i-1])} – {_fmt_pulgadas(cortes[i])}")

    grupos = [
        {
            "etiqueta": f"{etiquetas[i]} ({len(baldes[i])})",
            "color": PALETA_DIAMETRO[min(i, len(PALETA_DIAMETRO) - 1)],
            "ancho": ANCHOS_DIAMETRO[min(i, len(ANCHOS_DIAMETRO) - 1)],
            "features": baldes[i],
        }
        for i in range(len(baldes)) if baldes[i]
    ]

    if sin_dato:
        grupos.append({
            "etiqueta": f"sin dato ({len(sin_dato)})",
            "color": COLOR_SIN_DATO,
            "ancho": 1.0,
            "features": sin_dato,
        })

    return grupos


def mostrar_leyenda(grupos: list[dict], titulo: str):
    """Chips de color en una linea. `st.markdown` porque no hay widget nativo."""
    if not grupos:
        return

    chips = "".join(
        f'<span style="display:inline-block;margin:0 14px 4px 0;white-space:nowrap;">'
        f'<span style="display:inline-block;width:24px;'
        f'height:{max(2, round(g["ancho"]))}px;'
        f'background:rgb({g["color"][0]},{g["color"][1]},{g["color"][2]});'
        f'vertical-align:middle;margin-right:6px;border-radius:2px;"></span>'
        f'<span style="font-size:0.82rem;color:#444;">{g["etiqueta"]}</span></span>'
        for g in grupos
    )

    st.markdown(
        f'<div style="margin:-6px 0 10px 0;"><span style="font-size:0.78rem;'
        f'color:#888;margin-right:10px;">{titulo}</span>{chips}</div>',
        unsafe_allow_html=True,
    )


# ===========================================================================
# Posiciones inferidas
# ===========================================================================

def inferir_posiciones(nodos: pd.DataFrame, edges: pd.DataFrame,
                       max_pasadas: int = 4) -> pd.DataFrame:
    """
    Ubica los nodos sin coordenadas en el centroide de sus origenes.

    Un gasoducto no es un punto, es una linea; y una planta si es un lugar
    fisico pero no figura en ninguna capa oficial con ese nombre. En vez de
    dejarlos afuera del mapa, se los pone donde converge el gas que reciben:
    el promedio de las posiciones de sus origenes, ponderado por volumen.

    No es la ubicacion real. Es una posicion util para leer el mapa, y se
    marca como tal (columna `posicion`) para que nadie la confunda con un dato.

    Se resuelve en pasadas porque hay dependencias encadenadas: las plantas se
    alimentan de gasoductos que a su vez se acaban de inferir desde sus areas.
    Con 4 pasadas alcanza para la cascada actual (area -> ducto -> planta);
    corta antes si en una pasada no se resolvio nada nuevo.

    Returns
    -------
    pandas.DataFrame
        Copia de `nodos` con lat/lon completadas donde se pudo, mas la columna
        `posicion` con "cargada" o "inferida".
    """
    salida = nodos.copy()
    salida["posicion"] = salida.apply(
        lambda f: "cargada" if pd.notna(f["lat"]) and pd.notna(f["lon"]) else "",
        axis=1,
    )

    coords = {
        f.clave: (float(f.lat), float(f.lon))
        for f in salida.dropna(subset=["lat", "lon"]).itertuples()
    }

    aristas = edges.copy()
    aristas["k_origen"] = clave_cruce(aristas["origen"])
    aristas["k_destino"] = clave_cruce(aristas["destino"])
    aristas["valor"] = pd.to_numeric(aristas["valor"], errors="coerce").fillna(0)
    aristas = aristas[aristas["valor"] > 0]

    for _ in range(max_pasadas):
        pendientes = [k for k in salida["clave"] if k not in coords]

        if not pendientes:
            break

        resueltos = 0

        for clave in pendientes:
            entrantes = aristas[aristas["k_destino"] == clave]
            entrantes = entrantes[entrantes["k_origen"].isin(coords)]

            peso = float(entrantes["valor"].sum())

            if peso <= 0:
                continue

            lat = sum(coords[f.k_origen][0] * f.valor for f in entrantes.itertuples()) / peso
            lon = sum(coords[f.k_origen][1] * f.valor for f in entrantes.itertuples()) / peso

            coords[clave] = (lat, lon)
            resueltos += 1

        if not resueltos:
            break

    inferidos = salida["posicion"] == ""

    salida.loc[inferidos, "lat"] = salida.loc[inferidos, "clave"].map(
        lambda k: coords[k][0] if k in coords else None)
    salida.loc[inferidos, "lon"] = salida.loc[inferidos, "clave"].map(
        lambda k: coords[k][1] if k in coords else None)

    salida.loc[inferidos & salida["lat"].notna(), "posicion"] = "inferida"

    return salida


# ===========================================================================
# Flujos
# ===========================================================================

def preparar_flujos(edges: pd.DataFrame, nodos: pd.DataFrame):
    """
    Cruza las aristas origen->destino con las coordenadas.

    Returns
    -------
    flujos : pandas.DataFrame
        Solo las aristas con las DOS puntas georreferenciadas.
    faltantes : list[str]
        Nombres que aparecen en las aristas y no tienen lat/lon.
    """
    con_coord = nodos.dropna(subset=["lat", "lon"])
    coords = {
        fila.clave: [float(fila.lon), float(fila.lat)]
        for fila in con_coord.itertuples()
    }

    flujos = edges.copy()
    flujos = flujos[flujos["valor"].fillna(0) > 0]

    flujos["k_origen"] = clave_cruce(flujos["origen"])
    flujos["k_destino"] = clave_cruce(flujos["destino"])

    faltantes = sorted(
        set(flujos.loc[~flujos["k_origen"].isin(coords), "origen"].astype(str))
        | set(flujos.loc[~flujos["k_destino"].isin(coords), "destino"].astype(str))
    )

    flujos = flujos[
        flujos["k_origen"].isin(coords) & flujos["k_destino"].isin(coords)
    ].copy()

    if flujos.empty:
        return flujos, faltantes

    flujos["origen_lonlat"] = flujos["k_origen"].map(coords)
    flujos["destino_lonlat"] = flujos["k_destino"].map(coords)

    # Ancho por la RAIZ del volumen. En escala lineal hay dos ordenes de
    # magnitud entre el flujo mayor y el menor, y el mayor tapa todo lo demas.
    maximo = float(flujos["valor"].max())
    flujos["ancho"] = 1.0 + 11.0 * (flujos["valor"] / maximo) ** 0.5

    # Color como COLUMNA del DataFrame: para LineLayer pydeck resuelve nombres
    # de columna sin problema, a diferencia de los accesores anidados sobre
    # GeoJSON. Verde -> rojo segun el volumen relativo.
    def _color(v):
        t = (v / maximo) ** 0.5
        return [int(90 + 130 * t), int(160 - 120 * t), int(90 - 40 * t), 205]

    flujos["color"] = flujos["valor"].map(_color)

    flujos["etiqueta"] = (
        flujos["origen"].astype(str) + " → " + flujos["destino"].astype(str)
        + "  ·  " + flujos["valor"].map(lambda v: f"{v:,.0f}")
    )

    return flujos, faltantes


# ===========================================================================
# Capas
# ===========================================================================

def _capas(nodos, flujos, concesiones, ductos, mostrar_etiquetas,
           tridimensional=False):
    capas = []

    if concesiones:
        capas.append(pdk.Layer(
            "GeoJsonLayer",
            data=concesiones,
            stroked=True,
            filled=True,
            get_fill_color=[225, 228, 230, 90],
            get_line_color=[150, 158, 165, 200],
            line_width_min_pixels=0.6,
            pickable=True,
        ))

    # Una capa por grupo, con el color como CONSTANTE. Ver `agrupar_ductos`.
    for grupo in (ductos or []):
        capas.append(pdk.Layer(
            "GeoJsonLayer",
            data={"type": "FeatureCollection", "features": grupo["features"]},
            stroked=True,
            filled=False,
            get_line_color=grupo["color"],
            line_width_units="pixels",
            get_line_width=grupo["ancho"],
            line_width_min_pixels=grupo["ancho"],
            pickable=True,
        ))

    if len(flujos):
        if tridimensional:
            # ArcLayer teseliza cada arco en decenas de segmentos: es lindo
            # pero pesa, sobre todo con muchas aristas.
            capas.append(pdk.Layer(
                "ArcLayer",
                data=flujos,
                get_source_position="origen_lonlat",
                get_target_position="destino_lonlat",
                get_source_color=[90, 160, 90, 150],
                get_target_color=[200, 30, 40, 200],
                get_width="ancho",
                pickable=True,
                auto_highlight=True,
            ))
        else:
            capas.append(pdk.Layer(
                "LineLayer",
                data=flujos,
                get_source_position="origen_lonlat",
                get_target_position="destino_lonlat",
                get_color="color",
                get_width="ancho",
                width_units="pixels",
                pickable=True,
                auto_highlight=True,
            ))

    capas.append(pdk.Layer(
        "ScatterplotLayer",
        data=nodos,
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_radius="radio",
        radius_min_pixels=4,
        radius_max_pixels=20,
        pickable=True,
        stroked=True,
        get_line_color=[255, 255, 255, 220],
        line_width_min_pixels=1,
    ))

    if mostrar_etiquetas:
        # Solo plantas y gasoductos: 130 nombres de area encimados no se leen.
        capas.append(pdk.Layer(
            "TextLayer",
            data=nodos[nodos["tipo"] != "area"],
            get_position=["lon", "lat"],
            get_text="nombre",
            get_size=13,
            get_color=[20, 20, 20, 235],
            get_alignment_baseline="'bottom'",
            get_pixel_offset=[0, -14],
        ))

    return capas


# ===========================================================================
# Panel
# ===========================================================================

def _ayuda_sin_datos(que: str):
    st.warning(f"No encuentro `{que}`.")
    st.caption(
        "Se genera una sola vez con `scripts/preparar_geo.py`, a partir de los "
        "GeoJSON de concesiones y ductos que se bajan a mano (o se exportan del "
        "GIS interno). Mirá el docstring de ese script."
    )


def _elegir_red(resultados):
    """Red oficial, o la del sandbox si corriste una intervención sobre ductos.

    No hace falta darle coordenadas al ducto nuevo: `completar_gasoductos` e
    `inferir_posiciones` ya lo ubican en el centroide de sus orígenes ponderado
    por volumen, igual que a cualquier otro. Un ducto nuevo entra por el mismo
    camino que VMN.

    Si nunca corriste el sandbox, la clave no existe y esto devuelve la red
    oficial sin dibujar ningún control: el mapa queda igual que antes.
    """
    oficial = resultados.get("red_gasoductos")
    sandbox = st.session_state.get(CLAVE_RED_SANDBOX)

    if sandbox is None or len(sandbox) == 0:
        return oficial

    nuevos = sorted(set(sandbox["destino"].astype(str))
                    - set(oficial["destino"].astype(str))) if oficial is not None else []
    faltan = sorted(set(oficial["destino"].astype(str))
                    - set(sandbox["destino"].astype(str))) if oficial is not None else []

    detalle = []
    if nuevos:
        detalle.append(f"**+{len(nuevos)}**: {', '.join(nuevos[:3])}")
    if faltan:
        detalle.append(f"**−{len(faltan)}**: {', '.join(faltan[:3])}")

    usar = st.toggle(
        "Ver la red del sandbox", value=bool(nuevos or faltan),
        help="Dibuja la red con los ductos que agregaste o sacaste en el tab "
             "Plantas (sandbox), en vez de la corrida oficial.")

    if not usar:
        return oficial

    if detalle:
        st.caption("Red del sandbox — " + " · ".join(detalle))
    else:
        st.caption("Red del sandbox: mismos destinos, volúmenes redistribuidos.")

    return sandbox


def _cuerpo_mapa(resultados: dict, ruta_nodos=RUTA_NODOS):
    """Dibuja el tab de red sobre el mapa, con geodata 100% local."""
    st.subheader("Red de gasoductos")

    if pdk is None:
        st.error("Falta `pydeck`. Instalalo con `pip install pydeck`.")
        return

    edges = _elegir_red(resultados)

    if edges is None or len(edges) == 0:
        st.info("No hay flujos para este período.")
        return

    nodos = cargar_nodos(ruta_nodos)

    if nodos.empty:
        _ayuda_sin_datos(str(ruta_nodos))
        return

    # Los gasoductos casi nunca tienen coordenada cargada (son lineas). Sin
    # esto ninguna arista tiene sus dos puntas y el mapa sale sin un solo arco.
    nodos = completar_gasoductos(nodos, edges)

    inferir = st.checkbox(
        "Ubicar gasoductos y plantas donde converge su gas", value=True,
        help="Un gasoducto es una línea y una planta no figura en las capas "
             "oficiales. Se los ubica en el centroide de sus orígenes, "
             "ponderado por volumen. Es una posición de lectura, no un dato.")

    if inferir:
        nodos = inferir_posiciones(nodos, edges)
    else:
        nodos = nodos.assign(posicion="cargada")

    dibujables = nodos.dropna(subset=["lat", "lon"]).copy()

    if dibujables.empty:
        st.warning(f"`{ruta_nodos}` existe pero ninguna fila tiene lat/lon todavía.")
        return

    flujos, faltantes = preparar_flujos(edges, nodos)

    dibujables["color"] = dibujables["tipo"].map(COLOR_TIPO).apply(
        lambda c: c if isinstance(c, list) else COLOR_TIPO["area"])

    # Los inferidos van translucidos: se ven, pero se distinguen de un dato.
    dibujables.loc[dibujables["posicion"] == "inferida", "color"] = (
        dibujables.loc[dibujables["posicion"] == "inferida", "color"]
        .apply(lambda c: c[:3] + [110]))

    dibujables["radio"] = dibujables["tipo"].map(RADIO_TIPO).fillna(1500)

    dibujables["detalle"] = dibujables.apply(
        lambda f: f"{f['nombre']} ({f['tipo']}"
                  + (", posición inferida)" if f["posicion"] == "inferida" else ")"),
        axis=1)

    # Las posiciones derivadas se dibujan mas transparentes: son esquematicas y
    # no conviene que se lean igual que un dato cargado.
    if "derivada" in dibujables.columns:
        derivadas = dibujables["derivada"].fillna(False).astype(bool)
        dibujables.loc[derivadas, "color"] = dibujables.loc[derivadas, "color"].apply(
            lambda c: c[:3] + [110])

    concesiones = cargar_geojson(RUTA_CONCESIONES)
    ductos = cargar_geojson(RUTA_DUCTOS)

    # --- controles ---------------------------------------------------------
    c1, c2, c3, c4 = st.columns([1, 1, 1.4, 1])
    with c1:
        ver_conces = st.checkbox("Concesiones", value=concesiones is not None,
                                 disabled=concesiones is None)
    with c2:
        ver_ductos = st.checkbox("Trazas de ductos", value=ductos is not None,
                                 disabled=ductos is None)
    with c3:
        modo_color = st.selectbox(
            "Colorear ductos por", [MODO_SIN_COLOR, "Diámetro", "Empresa"],
            disabled=ductos is None or not ver_ductos,
            help="Sin colorear, las trazas van en una sola capa gris y el mapa "
                 "es notablemente más liviano. Agrupar dibuja una capa por "
                 "grupo: por diámetro son 4 o 5, por empresa pueden ser 30.")
    with c4:
        mostrar_etiquetas = st.checkbox("Nombres", value=True)

    grupos_ductos = []
    if ductos is not None and ver_ductos:
        grupos_ductos = agrupar_ductos(ductos, modo_color)

    c1, c2 = st.columns(2)
    with c1:
        solo_plantas = st.checkbox(
            "Solo flujos que terminan en planta", value=False,
            help="Filtra las aristas hacia gasoductos finales, que son la mayoría.")
    with c2:
        tridimensional = st.checkbox(
            "Vista 3D (arcos)", value=False,
            help="Los arcos se ven mejor pero pesan bastante más: cada uno se "
                 "dibuja con decenas de segmentos. En 2D son líneas rectas.")

    if solo_plantas:
        plantas = set(nodos.loc[nodos["tipo"] == "planta", "clave"])
        flujos = flujos[flujos["k_destino"].isin(plantas)]

    oeste, sur, este, norte = _bbox(dibujables)

    st.pydeck_chart(
        pdk.Deck(
            layers=_capas(
                dibujables, flujos,
                concesiones if ver_conces else None,
                grupos_ductos if ver_ductos else None,
                mostrar_etiquetas,
                tridimensional=tridimensional,
            ),
            initial_view_state=pdk.ViewState(
                latitude=(sur + norte) / 2,
                longitude=(oeste + este) / 2,
                zoom=6.2,
                pitch=45 if tridimensional else 0,
                bearing=0,
            ),
            # Sin basemap remoto: el firewall bloquea la salida y ademas el
            # contexto ya lo dan las concesiones.
            map_style=None,
            tooltip={"text": "{detalle}{etiqueta}{TIPO} {DIAMETRO}″ · {EMPRESA_IN}"},
        ),
        **ancho(),
    )

    if grupos_ductos and modo_color != MODO_SIN_COLOR:
        mostrar_leyenda(grupos_ductos, f"Ductos por {modo_color.lower()}:")

    # --- pie ---------------------------------------------------------------
    n_inferidos = int((dibujables["posicion"] == "inferida").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Nodos en el mapa", len(dibujables))
    c2.metric("Con posición inferida", n_inferidos)
    c3.metric("Flujos dibujados", len(flujos))
    c4.metric("Sin ubicar", len(faltantes))

    n_derivadas = int(dibujables.get("derivada", pd.Series(dtype=bool)).fillna(False).sum())
    if n_derivadas:
        st.caption(
            f"{n_derivadas} gasoductos se ubicaron en el baricentro de las áreas que "
            "les inyectan, ponderado por volumen. Es una posición esquemática, no la "
            "traza real: se dibujan más tenues. Cargales lat/lon en `geo_nodos.csv` "
            "para fijarlos."
        )

    if concesiones is None and ductos is None:
        st.info(
            "Se está dibujando sin fondo geográfico. Generá "
            "`datos/geo/concesiones.geojson` con `scripts/preparar_geo.py` "
            "para ver los polígonos de concesión."
        )

    if faltantes:
        with st.expander(f"{len(faltantes)} nodos sin coordenadas — no se dibujan"):
            st.caption(
                "Agregalos a `geo_nodos.csv` con su lat/lon. El nombre se cruza "
                "normalizado, así que no hace falta que coincida exacto."
            )
            st.code("\n".join(faltantes), language="text")

    st.caption(
        "El grosor del arco va con la raíz del volumen inyectado, no con el "
        "volumen: en escala lineal el flujo más grande tapa a todos los demás."
    )


# ===========================================================================
# Envoltorio
# ===========================================================================

def _envolver_en_fragment(funcion):
    """Envuelve el panel en `st.fragment` si esta version de Streamlit lo tiene.

    Sin esto, tocar cualquier checkbox del mapa rerunea el SCRIPT ENTERO: se
    redibujan los otros siete tabs con sus tablas y su graphviz, ademas del
    mapa. Con fragment, un toggle solo vuelve a dibujar el mapa.

    Se prueban los dos nombres porque `st.fragment` se llamo
    `st.experimental_fragment` entre 1.33 y 1.36, y se verifica que lo devuelto
    sea invocable: si no, se degrada al comportamiento de siempre en vez de
    romper el tab.
    """
    for nombre in ("fragment", "experimental_fragment"):
        decorador = getattr(st, nombre, None)
        if decorador is None:
            continue
        try:
            envuelta = decorador(funcion)
        except Exception:
            continue
        if callable(envuelta):
            return envuelta

    return funcion


panel_mapa = _envolver_en_fragment(_cuerpo_mapa)
