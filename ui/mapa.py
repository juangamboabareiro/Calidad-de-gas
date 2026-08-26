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
import math
from pathlib import Path

import pandas as pd
import streamlit as st

from ui.compat import ancho

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

# Color unico de las trazas. Se probo colorear por diametro y por empresa con
# una capa por grupo, y el costo de render no lo justifica: son varias
# GeoJsonLayer serializandose por separado en cada rerun. El desglose por
# empresa vive ahora en la tabla de abajo del mapa, que no cuesta nada.
VERSION = "2026-08-24d · sandbox: agregados en verde, eliminados en rojo"

COLOR_DUCTOS = [120, 135, 150, 200]
ANCHO_DUCTOS = 1.2

COLOR_SIN_DATO = [170, 170, 175, 150]

# Donde el tab "Plantas (sandbox)" deja su red modificada. Si nunca corriste el
# sandbox la clave no existe y el mapa se comporta exactamente como siempre:
# ni siquiera dibuja el control.
CLAVE_RED_SANDBOX = "sandbox_red_gasoductos"

# Estado de cada arista al comparar la red oficial con la del sandbox.
# Los eliminados NO se sacan del mapa: se repintan. El esquema general tiene que
# quedar fijo para poder leer QUE cambio, y un flujo que desaparece no se ve.
COLOR_AGREGADO = [0, 190, 120, 230]
COLOR_ELIMINADO = [200, 60, 70, 150]


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

def _largo_km(coords) -> float:
    """Largo de una polilinea, aproximacion equirectangular. Sobra a esta escala."""
    total = 0.0

    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        lat_media = math.radians((y0 + y1) / 2)
        dx = (x1 - x0) * math.cos(lat_media)
        dy = y1 - y0
        total += math.hypot(dx, dy)

    return total * 111.32


def _lineas_de(geom: dict) -> list:
    tipo = (geom or {}).get("type")

    if tipo == "LineString":
        return [geom["coordinates"]]
    if tipo == "MultiLineString":
        return geom["coordinates"]

    return []


@st.cache_data(show_spinner=False)
def _resumen_ductos(firma: tuple[str, float]) -> pd.DataFrame:
    """
    Tramos, kilometros y diametro por empresa.

    Va cacheado por firma del archivo porque recorre todos los vertices: con
    3.000 tramos es rapido, pero no hace falta rehacerlo en cada interaccion
    de la pagina.
    """
    ruta = Path(firma[0])

    if not ruta.exists():
        return pd.DataFrame()

    with open(ruta, encoding="utf-8") as f:
        geojson = json.load(f)

    filas = []

    for feat in geojson.get("features", []):
        props = feat.get("properties") or {}

        try:
            diametro = float(props.get("DIAMETRO"))
            if not (0.5 <= diametro <= 60):
                diametro = None
        except (TypeError, ValueError):
            diametro = None

        filas.append({
            "Empresa": str(props.get("EMPRESA_IN") or "Sin dato"),
            "km": sum(_largo_km(l) for l in _lineas_de(feat.get("geometry"))),
            "diametro": diametro,
        })

    if not filas:
        return pd.DataFrame()

    detalle = pd.DataFrame(filas)

    resumen = detalle.groupby("Empresa").agg(
        Tramos=("km", "size"),
        Km=("km", "sum"),
        **{"Diám. máx (″)": ("diametro", "max")},
        **{"Diám. mediano (″)": ("diametro", "median")},
    ).sort_values("Km", ascending=False)

    resumen["Km"] = resumen["Km"].round(0)

    return resumen.reset_index()


def resumen_ductos(ruta=None) -> pd.DataFrame:
    return _resumen_ductos(_firma(ruta or RUTA_DUCTOS))


# ===========================================================================
# Posiciones inferidas
# ===========================================================================

def _separar_coincidentes(coords: dict, nodos: pd.DataFrame, radio: float = 0.12):
    """
    Abanica los nodos inferidos que cayeron casi en el mismo punto.

    Varios gasoductos comparten el grueso de sus areas de origen, asi que sus
    centroides ponderados caen practicamente encimados. Sin esto, todas las
    lineas convergen a un pixel y las etiquetas se apilan: el efecto "estrella"
    ilegible. Se los reparte en circulo alrededor del punto comun, lo que no
    cambia nada del dato (la posicion ya era una aproximacion) y hace legible
    hacia donde va cada flujo.
    """
    inferidos = [k for k in nodos["clave"] if k in coords
                 and k not in set(nodos.dropna(subset=["lat", "lon"])["clave"])]

    grupos: dict[tuple, list] = {}

    for clave in inferidos:
        lat, lon = coords[clave]
        celda = (round(lat / radio), round(lon / radio))
        grupos.setdefault(celda, []).append(clave)

    for celda, claves in grupos.items():
        if len(claves) < 2:
            continue

        lat0 = sum(coords[k][0] for k in claves) / len(claves)
        lon0 = sum(coords[k][1] for k in claves) / len(claves)

        for i, clave in enumerate(sorted(claves)):
            angulo = 2 * math.pi * i / len(claves)
            coords[clave] = (
                lat0 + radio * math.sin(angulo),
                lon0 + radio * math.cos(angulo) / math.cos(math.radians(lat0)),
            )


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

    _separar_coincidentes(coords, salida)

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

    # Rango chico A PROPOSITO. Aunque se pida width_units="pixels", deck.gl
    # interpreta el ancho en METROS si esa prop no se aplica, y un valor de 11
    # se dibuja como una cuna de decenas de kilometros. El clamp de
    # width_max_pixels de mas abajo es la red de seguridad real.
    flujos["ancho"] = 0.8 + 4.2 * (flujos["valor"] / maximo) ** 0.5

    # Color como COLUMNA del DataFrame: para LineLayer pydeck resuelve nombres
    # de columna sin problema, a diferencia de los accesores anidados sobre
    # GeoJSON. Verde -> rojo segun el volumen relativo.
    def _color(v):
        t = (v / maximo) ** 0.5
        return [int(90 + 130 * t), int(160 - 120 * t), int(90 - 40 * t), 205]

    flujos["color"] = flujos["valor"].map(_color)

    # El estado del sandbox pisa el degradado por volumen: para leer que se
    # agrego y que se saco, el color tiene que significar eso y no el caudal.
    if "estado" in flujos.columns:
        flujos.loc[flujos["estado"] == "agregado", "color"] = \
            flujos.loc[flujos["estado"] == "agregado"].apply(
                lambda _: list(COLOR_AGREGADO), axis=1)
        flujos.loc[flujos["estado"] == "eliminado", "color"] = \
            flujos.loc[flujos["estado"] == "eliminado"].apply(
                lambda _: list(COLOR_ELIMINADO), axis=1)

    _marca = {"agregado": "  ·  AGREGADO en sandbox",
              "eliminado": "  ·  ELIMINADO en sandbox"}

    flujos["detalle"] = (
        flujos["origen"].astype(str) + " → " + flujos["destino"].astype(str)
        + "  ·  " + flujos["valor"].map(lambda v: f"{v:,.0f}")
    )

    if "estado" in flujos.columns:
        flujos["detalle"] = (
            flujos["detalle"] + flujos["estado"].map(_marca).fillna(""))

    return flujos, faltantes


# ===========================================================================
# Capas
# ===========================================================================

def _capas(nodos, flujos, concesiones, ductos, modo_etiquetas,
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

    if ductos:
        capas.append(pdk.Layer(
            "GeoJsonLayer",
            data=ductos,
            stroked=True,
            filled=False,
            get_line_color=COLOR_DUCTOS,
            line_width_units="pixels",
            get_line_width=ANCHO_DUCTOS,
            line_width_min_pixels=ANCHO_DUCTOS,
            line_width_max_pixels=2,
            # Sin pickable: son 3.000 features y el hit-testing en cada
            # movimiento del mouse es carisimo. El dato de los ductos esta en
            # la tabla de abajo.
            pickable=False,
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
                width_units="pixels",
                width_min_pixels=1,
                width_max_pixels=6,
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
                width_min_pixels=1,
                width_max_pixels=6,
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

    # Las areas nunca llevan etiqueta: son ~130 nombres y deck.gl no resuelve
    # colisiones de texto, asi que quedarian todos encimados. Los gasoductos si
    # se pueden mostrar desde que `_separar_coincidentes` los abanica; antes de
    # eso caian en el mismo punto y se leian superpuestos.
    if modo_etiquetas == "Plantas y gasoductos":
        texto = nodos[nodos["tipo"] != "area"]
    elif modo_etiquetas == "Plantas":
        texto = nodos[nodos["tipo"] == "planta"]
    else:
        texto = None

    if texto is not None and len(texto):
        capas.append(pdk.Layer(
            "TextLayer",
            data=texto,
            get_position=["lon", "lat"],
            get_text="nombre",
            get_size=13,
            get_color=[25, 25, 25, 240],
            get_alignment_baseline="'bottom'",
            get_pixel_offset=[0, -14],
            # Contorno claro: el texto oscuro sobre una concesion gris o sobre
            # el fondo negro del basemap se pierde igual de mal en los dos.
            font_settings={"sdf": True},
            outline_width=3,
            outline_color=[255, 255, 255, 220],
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


def combinar_redes(oficial, sandbox) -> pd.DataFrame:
    """
    Une la red oficial con la del sandbox y marca el estado de cada arista.

    estado
    ------
    base        en las dos. Se usa el volumen del sandbox, que es el vigente.
    agregado    solo en el sandbox: un ducto o una ruta que inventaste.
    eliminado   solo en la oficial: la sacaste en el sandbox.

    Los eliminados se conservan a proposito. Si se filtraran, el mapa mostraria
    una red mas chica y no habria forma de ver que se saco: un flujo ausente es
    invisible. Repintados en rojo, el esquema general queda fijo y el cambio se
    lee de un golpe.
    """
    def _clavear(df):
        if df is None or not len(df):
            return pd.DataFrame(columns=["origen", "destino", "valor", "_k"])
        salida = df.copy()
        salida["valor"] = pd.to_numeric(salida["valor"], errors="coerce").fillna(0)
        salida["_k"] = (clave_cruce(salida["origen"]) + "|"
                        + clave_cruce(salida["destino"]))
        return salida

    ofi, sbx = _clavear(oficial), _clavear(sandbox)

    claves_ofi, claves_sbx = set(ofi["_k"]), set(sbx["_k"])

    base = sbx[sbx["_k"].isin(claves_ofi)].assign(estado="base")
    agregados = sbx[~sbx["_k"].isin(claves_ofi)].assign(estado="agregado")
    eliminados = ofi[~ofi["_k"].isin(claves_sbx)].assign(estado="eliminado")

    union = pd.concat([base, agregados, eliminados], ignore_index=True)

    return union.drop(columns="_k")


def agregar_nodos_faltantes(nodos: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """
    Da de alta los nodos que aparecen en las aristas y no estan en el CSV.

    Es lo que hace que un ducto inventado en el sandbox llegue al mapa.
    `inferir_posiciones` recorre los NODOS, no las aristas: si el nombre no
    figura en `geo_nodos.csv` no existe para el mapa, y su flujo no se dibuja
    aunque la arista este ahi.

    El tipo se asume gasoducto: es lo que se agrega desde el sandbox, y ademas
    es el tipo que se ubica por inferencia sin pedirle coordenadas a nadie.
    """
    if edges is None or not len(edges):
        return nodos

    conocidos = set(nodos["clave"])

    nombres = {}
    for col in ("origen", "destino"):
        for nombre in edges[col].dropna().astype(str):
            nombres.setdefault(_clave_de(nombre), nombre)

    nuevos = [
        {"nombre": nombre, "tipo": "gasoducto", "lat": None, "lon": None,
         "clave": clave, "fuente": "sandbox"}
        for clave, nombre in nombres.items() if clave not in conocidos
    ]

    if not nuevos:
        return nodos

    print(f"[mapa] {len(nuevos)} nodos nuevos desde las aristas: "
          f"{[n['nombre'] for n in nuevos]}")

    return pd.concat([nodos, pd.DataFrame(nuevos)], ignore_index=True)


def _clave_de(texto: str) -> str:
    return clave_cruce(pd.Series([texto])).iloc[0]


def _elegir_red(resultados):
    """Red del mapa: la oficial, o la union oficial+sandbox si intervino algo.

    No hay que darle coordenadas al ducto nuevo: `agregar_nodos_faltantes` lo da
    de alta y `inferir_posiciones` lo ubica en el centroide de sus origenes
    ponderado por volumen, igual que a cualquier otro.

    Si nunca corriste el sandbox, la clave no existe en `session_state` y esto
    devuelve la red oficial sin dibujar ningun control.
    """
    oficial = resultados.get("red_gasoductos")
    sandbox = st.session_state.get(CLAVE_RED_SANDBOX)

    if sandbox is None or len(sandbox) == 0:
        if oficial is None:
            return None
        return oficial.assign(estado="base")

    union = combinar_redes(oficial, sandbox)

    conteo = union["estado"].value_counts()
    agregados = int(conteo.get("agregado", 0))
    eliminados = int(conteo.get("eliminado", 0))

    usar = st.checkbox(
        "Ver los cambios del sandbox", value=bool(agregados or eliminados),
        help="Superpone la red del tab Plantas (sandbox) sobre la oficial. "
             "Lo agregado va en verde, lo eliminado en rojo. Nada se saca del "
             "esquema: se repinta.")

    if not usar:
        return oficial.assign(estado="base") if oficial is not None else None

    if agregados or eliminados:
        partes = []
        if agregados:
            partes.append(f"**{agregados}** agregada(s)")
        if eliminados:
            partes.append(f"**{eliminados}** eliminada(s)")
        st.caption("Sandbox: " + " · ".join(partes))
    else:
        st.caption("Sandbox: mismas rutas, volúmenes redistribuidos.")

    return union


def _cuerpo_mapa(resultados: dict, ruta_nodos=RUTA_NODOS):
    """Dibuja el tab de red sobre el mapa, con geodata 100% local."""
    st.subheader("Red de gasoductos")
    st.caption(f"ui/mapa.py · {VERSION}")

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

    # Un ducto inventado en el sandbox no esta en geo_nodos.csv, y sin nodo su
    # arista no se dibuja. Se lo da de alta desde las propias aristas.
    nodos = agregar_nodos_faltantes(nodos, edges)

    # Los gasoductos y las plantas no tienen coordenada propia: los ubica
    # `inferir_posiciones`, que ademas abanica los que caen en el mismo punto.
    #
    # Hubo aca una `completar_gasoductos` que hacia la mitad de ese trabajo
    # antes, y rompia el resto: dejaba los gasoductos marcados como posicion
    # "cargada", asi que `_separar_coincidentes` los daba por dato real y no
    # los separaba. Como varios comparten las mismas areas de origen, caian
    # todos en el mismo pixel, y las plantas inferidas desde ellos tambien.
    # TTY quedaba dibujada pero enterrada bajo los marcadores de VMN y VMS.
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
    c1, c2, c3 = st.columns(3)
    with c1:
        ver_conces = st.checkbox("Concesiones", value=concesiones is not None,
                                 disabled=concesiones is None)
    with c2:
        ver_ductos = st.checkbox("Trazas de ductos", value=ductos is not None,
                                 disabled=ductos is None)
    with c3:
        modo_etiquetas = st.selectbox(
            "Nombres", ["Plantas y gasoductos", "Plantas", "Ninguno"],
            help="Las áreas nunca se etiquetan: son ~130 nombres y se pisarían "
                 "entre sí.")

    c1, c2 = st.columns(2)
    with c1:
        solo_plantas = st.checkbox(
            "Solo flujos que terminan en planta", value=True,
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
                ductos if ver_ductos else None,
                modo_etiquetas,
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
            # Un solo campo: si el tooltip nombra claves que la capa no tiene, deck.gl
            # las imprime literales ("{TIPO}"). Cada capa trae su propio `detalle`.
            tooltip={"text": "{detalle}"},
        ),
        **ancho(),
    )

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

    if "estado" in flujos.columns and set(flujos["estado"]) - {"base"}:
        st.markdown(
            '<div style="margin:-6px 0 10px 0;font-size:0.82rem;color:#444;">'
            '<span style="display:inline-block;width:22px;height:4px;'
            f'background:rgb({COLOR_AGREGADO[0]},{COLOR_AGREGADO[1]},{COLOR_AGREGADO[2]});'
            'vertical-align:middle;margin-right:6px;border-radius:2px;"></span>'
            'agregado en sandbox'
            '<span style="display:inline-block;width:22px;height:4px;'
            f'background:rgb({COLOR_ELIMINADO[0]},{COLOR_ELIMINADO[1]},{COLOR_ELIMINADO[2]});'
            'vertical-align:middle;margin:0 6px 0 20px;border-radius:2px;"></span>'
            'eliminado en sandbox (se conserva en el esquema)</div>',
            unsafe_allow_html=True,
        )

    st.caption(
        "El grosor de la línea va con la raíz del volumen inyectado, no con el "
        "volumen: en escala lineal el flujo más grande tapa a todos los demás."
    )

    if ductos is not None:
        st.divider()
        st.markdown("#### Gasoductos por empresa")

        resumen = resumen_ductos()

        if resumen.empty:
            st.caption("No hay trazas cargadas.")
        else:
            izq, der = st.columns([3, 1])

            with izq:
                st.dataframe(
                    resumen.style.format({
                        "Km": "{:,.0f}",
                        "Diám. máx (″)": "{:,.0f}",
                        "Diám. mediano (″)": "{:,.1f}",
                    }),
                    **ancho(),
                    hide_index=True,
                )
            with der:
                st.metric("Empresas", len(resumen))
                st.metric("Tramos", f"{int(resumen['Tramos'].sum()):,}")
                st.metric("Km totales", f"{resumen['Km'].sum():,.0f}")

            st.caption(
                "Sobre las trazas que quedaron después del filtro de "
                "`preparar_geo.py`, no sobre la capa completa. Los kilómetros "
                "son de la geometría simplificada, así que están subestimados "
                "en torno al 1%."
            )


# ===========================================================================
# Envoltorio
# ===========================================================================

def _envolver_en_fragment(funcion):
    """Envuelve el panel en `st.fragment` si esta version de Streamlit lo tiene.

    Sin esto, tocar cualquier checkbox del mapa rerunea el SCRIPT ENTERO: se
    redibujan los otros tabs con sus tablas y su graphviz, ademas del mapa. Con
    fragment, un toggle solo vuelve a dibujar el mapa.

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


# `app.py` sigue importando `panel_mapa`: la firma publica no cambia.
panel_mapa = _envolver_en_fragment(_cuerpo_mapa)
