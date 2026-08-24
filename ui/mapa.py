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


DIR_GEO = Path("datos") / "geo"
RUTA_NODOS = Path("datos") / "geo_nodos.csv"
RUTA_CONCESIONES = DIR_GEO / "concesiones.geojson"
RUTA_DUCTOS = DIR_GEO / "ductos.geojson"

COLOR_TIPO = {
    "planta": [200, 30, 40, 230],
    "gasoducto": [30, 90, 160, 210],
    "area": [90, 160, 90, 190],
}

RADIO_TIPO = {"planta": 4500, "gasoducto": 2800, "area": 1500}


# ===========================================================================
# Carga
# ===========================================================================

@st.cache_data(show_spinner=False)
def cargar_nodos(ruta=RUTA_NODOS) -> pd.DataFrame:
    """Lee geo_nodos.csv. Vacio si todavia no existe."""
    ruta = Path(ruta)

    if not ruta.exists():
        return pd.DataFrame(columns=["nombre", "tipo", "lat", "lon", "clave"])

    nodos = pd.read_csv(ruta, comment="#")

    for col in ("lat", "lon"):
        nodos[col] = pd.to_numeric(nodos.get(col), errors="coerce")

    nodos["tipo"] = nodos.get("tipo", "area").fillna("area").str.strip().str.lower()
    nodos["clave"] = clave_cruce(nodos["nombre"])

    return nodos


@st.cache_data(show_spinner=False)
def cargar_geojson(ruta) -> dict | None:
    """Lee un GeoJSON local. None si no esta."""
    ruta = Path(ruta)

    if not ruta.exists():
        return None

    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def _bbox(puntos: pd.DataFrame, margen: float = 0.4):
    return (
        float(puntos["lon"].min()) - margen,
        float(puntos["lat"].min()) - margen,
        float(puntos["lon"].max()) + margen,
        float(puntos["lat"].max()) + margen,
    )


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
            get_line_color=[120, 120, 130, 220],
            line_width_min_pixels=1.4,
            pickable=False,
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
    st.warning(f"Falta `{que}`.")
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

    dibujables = nodos.dropna(subset=["lat", "lon"]).copy()

    if dibujables.empty:
        st.warning(f"`{ruta_nodos}` existe pero ninguna fila tiene lat/lon todavía.")
        return

    flujos, faltantes = preparar_flujos(edges, nodos)

    dibujables["color"] = dibujables["tipo"].map(COLOR_TIPO).apply(
        lambda c: c if isinstance(c, list) else COLOR_TIPO["area"])
    dibujables["radio"] = dibujables["tipo"].map(RADIO_TIPO).fillna(1500)

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
        mostrar_etiquetas = st.checkbox("Nombres", value=True)

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
            tooltip={"text": "{nombre}{etiqueta}"},
        ),
        use_container_width=True,
    )

    # --- pie ---------------------------------------------------------------
    c1, c2, c3 = st.columns(3)
    c1.metric("Nodos en el mapa", len(dibujables))
    c2.metric("Flujos dibujados", len(flujos))
    c3.metric("Sin coordenadas", len(faltantes))

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
