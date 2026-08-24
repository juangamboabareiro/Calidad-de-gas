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

# Cortes de diametro en pulgadas. El ultimo tramo es abierto.
# Los troncales de exportacion de Neuquen estan en 24-36"; de 10 para abajo son
# ramales y lineas de gathering.
ESCALA_DIAMETRO = [
    (12, [150, 175, 190, 170], 1.0, "menos de 12″"),
    (20, [ 70, 150, 165, 200], 1.8, "12″ a 20″"),
    (30, [ 40, 100, 170, 220], 2.8, "20″ a 30″"),
    (999, [140,  40, 110, 240], 4.0, "30″ o más"),
]

# Paleta categorica para colorear por empresa. Se cicla si hay mas empresas.
PALETA_EMPRESAS = [
    [ 31, 119, 180, 220], [255, 127,  14, 220], [ 44, 160,  44, 220],
    [214,  39,  40, 220], [148, 103, 189, 220], [140,  86,  75, 220],
    [227, 119, 194, 220], [127, 127, 127, 220], [188, 189,  34, 220],
    [ 23, 190, 207, 220],
]

COLOR_SIN_DATO = [170, 170, 175, 150]


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

def _tramo_diametro(valor):
    """(color, ancho, etiqueta) segun el diametro, o el default si no hay dato."""
    try:
        d = float(valor)
    except (TypeError, ValueError):
        return COLOR_SIN_DATO, 1.0, "sin dato"

    # La capa usa centinelas para "sin dato" (hay valores de 9999). Un ducto
    # real no pasa de ~48 pulgadas, asi que todo lo de arriba es ruido.
    if d <= 0 or d > 60:
        return COLOR_SIN_DATO, 1.0, "sin dato"

    for tope, color, ancho, etiqueta in ESCALA_DIAMETRO:
        if d < tope:
            return color, ancho, etiqueta

    return COLOR_SIN_DATO, 1.0, "sin dato"


def colorear_ductos(geojson: dict, modo: str) -> tuple[dict, list[tuple[str, list]]]:
    """
    Devuelve una copia con `color` y `ancho` en las propiedades, mas la leyenda.

    No muta el original: `cargar_geojson` esta cacheado y modificar el dict
    ensuciaria el cache para el resto de la sesion. Se arman properties nuevas
    pero se comparte la geometria, que es lo pesado y ademas es de solo lectura.

    Returns
    -------
    (geojson, leyenda) : (dict, list[(etiqueta, color)])
    """
    features = []
    leyenda: dict[str, list] = {}

    if modo == "Empresa":
        empresas = sorted({
            str(f.get("properties", {}).get("EMPRESA_IN") or "Sin dato")
            for f in geojson.get("features", [])
        })
        colores = {
            e: (COLOR_SIN_DATO if e == "Sin dato"
                else PALETA_EMPRESAS[i % len(PALETA_EMPRESAS)])
            for i, e in enumerate(empresas)
        }

    for feat in geojson.get("features", []):
        props = feat.get("properties", {}) or {}

        if modo == "Empresa":
            clave = str(props.get("EMPRESA_IN") or "Sin dato")
            color, ancho = colores[clave], 1.8
        else:
            color, ancho, clave = _tramo_diametro(props.get("DIAMETRO"))

        leyenda.setdefault(clave, color)

        features.append({
            "type": "Feature",
            "properties": {**props, "color": color, "ancho": ancho},
            "geometry": feat["geometry"],
        })

    if modo == "Empresa":
        orden = sorted(leyenda.items(), key=lambda kv: kv[0])
    else:
        posicion = {e: i for i, (_, _, _, e) in enumerate(ESCALA_DIAMETRO)}
        orden = sorted(leyenda.items(), key=lambda kv: posicion.get(kv[0], 99))

    return {"type": "FeatureCollection", "features": features}, orden


def mostrar_leyenda(leyenda: list[tuple[str, list]], titulo: str):
    """Chips de color en una linea. `st.markdown` porque no hay widget nativo."""
    if not leyenda:
        return

    chips = "".join(
        f'<span style="display:inline-block;margin:0 14px 4px 0;white-space:nowrap;">'
        f'<span style="display:inline-block;width:22px;height:4px;'
        f'background:rgb({c[0]},{c[1]},{c[2]});vertical-align:middle;'
        f'margin-right:6px;border-radius:2px;"></span>'
        f'<span style="font-size:0.82rem;color:#444;">{etiqueta}</span></span>'
        for etiqueta, c in leyenda
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

    flujos["etiqueta"] = (
        flujos["origen"].astype(str) + " → " + flujos["destino"].astype(str)
        + "  ·  " + flujos["valor"].map(lambda v: f"{v:,.0f}")
    )

    return flujos, faltantes


# ===========================================================================
# Capas
# ===========================================================================

def _capas(nodos, flujos, concesiones, ductos, mostrar_etiquetas):
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

    if ductos:
        capas.append(pdk.Layer(
            "GeoJsonLayer",
            data=ductos,
            stroked=True,
            filled=False,
            get_line_color="properties.color",
            get_line_width="properties.ancho",
            line_width_units="pixels",
            line_width_min_pixels=0.8,
            pickable=True,
        ))

    if len(flujos):
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


def panel_mapa(resultados: dict, ruta_nodos=RUTA_NODOS):
    """Dibuja el tab de red sobre el mapa, con geodata 100% local."""
    st.subheader("Red de gasoductos")

    if pdk is None:
        st.error("Falta `pydeck`. Instalalo con `pip install pydeck`.")
        return

    edges = resultados.get("red_gasoductos")

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
            "Colorear ductos por", ["Diámetro", "Empresa"],
            disabled=ductos is None or not ver_ductos,
            help="Por diámetro se distingue un troncal de un ramal; "
                 "por empresa, quién opera cada traza.")
    with c4:
        mostrar_etiquetas = st.checkbox("Nombres", value=True)

    leyenda = []
    if ductos is not None and ver_ductos:
        ductos, leyenda = colorear_ductos(ductos, modo_color)

    solo_plantas = st.checkbox(
        "Solo flujos que terminan en planta", value=False,
        help="Filtra las aristas hacia gasoductos finales, que son la mayoría.")

    if solo_plantas:
        plantas = set(nodos.loc[nodos["tipo"] == "planta", "clave"])
        flujos = flujos[flujos["k_destino"].isin(plantas)]

    oeste, sur, este, norte = _bbox(dibujables)

    st.pydeck_chart(
        pdk.Deck(
            layers=_capas(
                dibujables, flujos,
                concesiones if ver_conces else None,
                ductos if ver_ductos else None,
                mostrar_etiquetas,
            ),
            initial_view_state=pdk.ViewState(
                latitude=(sur + norte) / 2,
                longitude=(oeste + este) / 2,
                zoom=6.2,
                pitch=35,
            ),
            # Sin basemap remoto: el firewall bloquea la salida y ademas el
            # contexto ya lo dan las concesiones.
            map_style=None,
            tooltip={"text": "{detalle}{etiqueta}{TIPO} {DIAMETRO}″ · {EMPRESA_IN}"},
        ),
        use_container_width=True,
    )

    if leyenda:
        mostrar_leyenda(leyenda, f"Ductos por {modo_color.lower()}:")

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
