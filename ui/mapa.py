"""
Mapa de la red: areas, gasoductos y plantas sobre el territorio real.

Que reemplaza
-------------
El tab "Red de Gasoductos" dibujaba un grafo de graphviz con ~140 aristas
Area -> Gasoducto. Sin geografia y con esa cantidad de nodos queda ilegible, y
ademas no dice nada que la tabla no diga mejor. Aca se cambia por un mapa.

De donde sale el fondo
----------------------
Del servicio WMS publico de la Secretaria de Energia (sig.se.gob.ar/wmsenergia).
Las capas que importan:

    planosbase_concesiones_explotacion   poligonos de concesion CON los nombres
    provincia_neuquen_gasoductos         trazas de gasoductos de Neuquen
    enargas_gasoductos_distribucion      red de transporte y distribucion

No hay que descargar nada: deck.gl pide la imagen directo al WMS y la pone como
capa de fondo (`BitmapLayer`). Si el servicio no responde, el mapa igual se
dibuja sobre el basemap de Carto, solo que sin las concesiones.

Que hay que cargar
------------------
`datos/geo_nodos.csv`, con una fila por area / gasoducto / planta:

    nombre,tipo,lat,lon,fuente,notas
    Fortin de Piedra,area,-38.12,-68.94,centroide concesion,
    TTY,planta,,,,falta cargar

Las filas sin lat/lon no se dibujan y se listan en pantalla. El mapa funciona
desde el primer nodo cargado; no hace falta completar los 130.

Para llenarlo automaticamente ver `scripts/geo_desde_concesiones.py`, que saca
los centroides del shapefile oficial y matchea por nombre con la misma tabla de
alias que usa el pipeline.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    import pydeck as pdk
except ImportError:  # pragma: no cover
    pdk = None

from pipeline.cromatografia import clave_cruce


RUTA_GEO = Path("datos") / "geo_nodos.csv"

WMS_ENERGIA = "https://sig.se.gob.ar/wmsenergia"

CAPAS_WMS = {
    "Concesiones de explotación": "planosbase_concesiones_explotacion",
    "Gasoductos de Neuquén": "provincia_neuquen_gasoductos",
    "Red ENARGAS (transporte)": "enargas_gasoductos_distribucion",
}

# Basemap sin token: estilo Carto Positron.
ESTILO_BASE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"

COLOR_TIPO = {
    "planta": [200, 30, 40, 220],
    "gasoducto": [30, 90, 160, 200],
    "area": [90, 160, 90, 180],
}

RADIO_TIPO = {"planta": 4200, "gasoducto": 2600, "area": 1600}


# ===========================================================================
# Carga de coordenadas
# ===========================================================================

@st.cache_data(show_spinner=False)
def cargar_geo(ruta=RUTA_GEO) -> pd.DataFrame:
    """
    Lee `geo_nodos.csv`. Devuelve vacio si el archivo no existe todavia.

    Returns
    -------
    pandas.DataFrame
        Columnas nombre, tipo, lat, lon, clave (para cruzar con el modelo).
    """
    ruta = Path(ruta)

    if not ruta.exists():
        return pd.DataFrame(columns=["nombre", "tipo", "lat", "lon", "clave"])

    geo = pd.read_csv(ruta, comment="#")

    for col in ("lat", "lon"):
        geo[col] = pd.to_numeric(geo.get(col), errors="coerce")

    geo["tipo"] = geo.get("tipo", "area").fillna("area").str.strip().str.lower()
    geo["clave"] = clave_cruce(geo["nombre"])

    return geo


def _mercator(lat: float, lon: float) -> tuple[float, float]:
    """lat/lon -> EPSG:3857 en metros."""
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)

    return x, y * 20037508.34 / 180.0


def url_wms(capas: list[str], bbox_ll: tuple[float, float, float, float],
            ancho: int = 1600, alto: int = 1600) -> str:
    """
    Arma el GetMap del WMS para el bbox dado.

    Se pide en EPSG:3857 y no en 4326 a proposito: `BitmapLayer` ubica la imagen
    en un rectangulo ya proyectado a Web Mercator, asi que una imagen en
    lat/lon plano quedaria estirada verticalmente. Pidiendola en 3857 coincide.

    Parameters
    ----------
    bbox_ll : (oeste, sur, este, norte) en grados.
    """
    oeste, sur, este, norte = bbox_ll

    x0, y0 = _mercator(sur, oeste)
    x1, y1 = _mercator(norte, este)

    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": ",".join(capas),
        "SRS": "EPSG:3857",
        "BBOX": f"{x0},{y0},{x1},{y1}",
        "WIDTH": str(ancho),
        "HEIGHT": str(alto),
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
    }

    return WMS_ENERGIA + "?" + "&".join(f"{k}={v}" for k, v in params.items())


def _bbox(puntos: pd.DataFrame, margen: float = 0.35) -> tuple[float, float, float, float]:
    """Rectangulo que contiene todos los nodos, con un margen en grados."""
    return (
        float(puntos["lon"].min()) - margen,
        float(puntos["lat"].min()) - margen,
        float(puntos["lon"].max()) + margen,
        float(puntos["lat"].max()) + margen,
    )


# ===========================================================================
# Armado de capas
# ===========================================================================

def preparar_flujos(edges: pd.DataFrame, geo: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """
    Cruza las aristas origen->destino con las coordenadas.

    Parameters
    ----------
    edges : pandas.DataFrame
        Columnas origen, destino, valor (lo que ya arma `red_gasoductos`).
    geo : pandas.DataFrame
        Salida de `cargar_geo`.

    Returns
    -------
    flujos : pandas.DataFrame
        Solo las aristas con las DOS puntas georreferenciadas, con columnas
        origen_lonlat / destino_lonlat listas para el ArcLayer.
    sin_coordenadas : list[str]
        Nombres que aparecen en las aristas y no tienen lat/lon.
    """
    con_coord = geo.dropna(subset=["lat", "lon"])
    mapa = {
        fila.clave: [float(fila.lon), float(fila.lat)]
        for fila in con_coord.itertuples()
    }

    flujos = edges.copy()
    flujos = flujos[flujos["valor"].fillna(0) > 0]

    flujos["k_origen"] = clave_cruce(flujos["origen"])
    flujos["k_destino"] = clave_cruce(flujos["destino"])

    faltan = sorted(
        {k for k in flujos["k_origen"] if k not in mapa}
        | {k for k in flujos["k_destino"] if k not in mapa}
    )

    nombres_faltantes = sorted(
        set(flujos.loc[~flujos["k_origen"].isin(mapa), "origen"])
        | set(flujos.loc[~flujos["k_destino"].isin(mapa), "destino"])
    )

    flujos = flujos[
        flujos["k_origen"].isin(mapa) & flujos["k_destino"].isin(mapa)
    ].copy()

    if flujos.empty:
        return flujos, nombres_faltantes

    flujos["origen_lonlat"] = flujos["k_origen"].map(mapa)
    flujos["destino_lonlat"] = flujos["k_destino"].map(mapa)

    # Ancho proporcional a la raiz del volumen: con lineal, el flujo mas grande
    # tapa todo lo demas (hay dos ordenes de magnitud entre el mayor y el menor).
    maximo = float(flujos["valor"].max())
    flujos["ancho"] = 1.0 + 11.0 * (flujos["valor"] / maximo) ** 0.5

    flujos["etiqueta"] = (
        flujos["origen"].astype(str) + " → " + flujos["destino"].astype(str)
    )

    return flujos, nombres_faltantes


def _capas(geo_dibujable, flujos, capas_wms, mostrar_etiquetas):
    capas = []

    if capas_wms:
        capas.append(
            pdk.Layer(
                "BitmapLayer",
                data=None,
                image=url_wms(capas_wms, _bbox(geo_dibujable)),
                bounds=list(_bbox(geo_dibujable)),
                opacity=0.55,
            )
        )

    if len(flujos):
        capas.append(
            pdk.Layer(
                "ArcLayer",
                data=flujos,
                get_source_position="origen_lonlat",
                get_target_position="destino_lonlat",
                get_source_color=[90, 160, 90, 150],
                get_target_color=[200, 30, 40, 190],
                get_width="ancho",
                pickable=True,
                auto_highlight=True,
            )
        )

    capas.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=geo_dibujable,
            get_position=["lon", "lat"],
            get_fill_color="color",
            get_radius="radio",
            radius_min_pixels=4,
            radius_max_pixels=22,
            pickable=True,
            stroked=True,
            get_line_color=[255, 255, 255, 200],
            line_width_min_pixels=1,
        )
    )

    if mostrar_etiquetas:
        capas.append(
            pdk.Layer(
                "TextLayer",
                data=geo_dibujable[geo_dibujable["tipo"] != "area"],
                get_position=["lon", "lat"],
                get_text="nombre",
                get_size=13,
                get_color=[25, 25, 25, 230],
                get_alignment_baseline="'bottom'",
                get_pixel_offset=[0, -14],
            )
        )

    return capas


# ===========================================================================
# Panel
# ===========================================================================

def panel_mapa(resultados: dict, ruta_geo=RUTA_GEO):
    """
    Dibuja el tab de red sobre el mapa.

    Degrada de a poco: sin pydeck avisa como instalarlo, sin archivo de
    coordenadas explica que cargar, y con coordenadas parciales dibuja lo que
    hay y lista lo que falta. En ningun caso deja el tab en blanco.
    """
    st.subheader("Red de gasoductos")

    if pdk is None:
        st.error("Falta `pydeck`. Instalalo con `pip install pydeck`.")
        return

    edges = resultados.get("red_gasoductos")

    if edges is None or len(edges) == 0:
        st.info("No hay flujos para este período.")
        return

    geo = cargar_geo(ruta_geo)

    if geo.empty:
        st.warning(
            f"Todavía no existe `{ruta_geo}`. El mapa necesita una fila por "
            "área / gasoducto / planta con sus coordenadas."
        )
        st.caption(
            "Podés generarlo con `scripts/geo_desde_concesiones.py`, que saca los "
            "centroides del shapefile oficial de concesiones y los matchea por "
            "nombre con la tabla de alias del pipeline."
        )
        return

    flujos, faltantes = preparar_flujos(edges, geo)

    geo_dibujable = geo.dropna(subset=["lat", "lon"]).copy()

    if geo_dibujable.empty:
        st.warning("El archivo de coordenadas existe pero no tiene ninguna fila con lat/lon.")
        return

    geo_dibujable["color"] = geo_dibujable["tipo"].map(COLOR_TIPO).apply(
        lambda c: c if isinstance(c, list) else COLOR_TIPO["area"])
    geo_dibujable["radio"] = geo_dibujable["tipo"].map(RADIO_TIPO).fillna(1600)

    # --- controles ---------------------------------------------------------
    c1, c2 = st.columns([3, 2])
    with c1:
        elegidas = st.multiselect(
            "Capas oficiales de fondo (WMS Secretaría de Energía)",
            list(CAPAS_WMS.keys()),
            default=["Concesiones de explotación"],
        )
    with c2:
        mostrar_etiquetas = st.checkbox("Nombres de plantas y gasoductos", value=True)
        solo_plantas = st.checkbox("Solo flujos que terminan en planta", value=False)

    if solo_plantas:
        plantas = set(geo.loc[geo["tipo"] == "planta", "clave"])
        flujos = flujos[flujos["k_destino"].isin(plantas)]

    capas_wms = [CAPAS_WMS[n] for n in elegidas]

    oeste, sur, este, norte = _bbox(geo_dibujable)

    vista = pdk.ViewState(
        latitude=(sur + norte) / 2,
        longitude=(oeste + este) / 2,
        zoom=6.2,
        pitch=35,
    )

    st.pydeck_chart(
        pdk.Deck(
            layers=_capas(geo_dibujable, flujos, capas_wms, mostrar_etiquetas),
            initial_view_state=vista,
            map_style=ESTILO_BASE,
            tooltip={"text": "{nombre}\n{etiqueta}"},
        ),
        use_container_width=True,
    )

    # --- pie ---------------------------------------------------------------
    c1, c2, c3 = st.columns(3)
    c1.metric("Nodos en el mapa", len(geo_dibujable))
    c2.metric("Flujos dibujados", len(flujos))
    c3.metric("Sin coordenadas", len(faltantes))

    if faltantes:
        with st.expander(f"{len(faltantes)} nodos sin coordenadas — no se dibujan"):
            st.caption(
                "Agregalos a `geo_nodos.csv` con su lat/lon. El nombre se cruza "
                "normalizado, así que no hace falta que coincida exacto."
            )
            st.code("\n".join(faltantes), language="text")

    st.caption(
        "El grosor del arco va con la raíz del volumen inyectado, no con el "
        "volumen: en escala lineal el flujo más grande tapa a todos los demás. "
        "Fondo: WMS público de la Secretaría de Energía."
    )
