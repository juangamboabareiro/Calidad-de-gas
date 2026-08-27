"""
Tab "Graphs" — réplica del dashboard GRAPHS.pdf del cliente.
============================================================

Uso desde app.py:

    from ui.tab_graphs import panel_graphs
    ...
    with tab_graphs:
        panel_graphs(resultados,
                     serie=st.session_state.get("serie"),
                     fallos=st.session_state.get("serie_fallos"))

`serie` es el dict {"plantas", "areas", "pool"} que arma `ejecutar_serie()` en
app.py corriendo el pipeline mes a mes (ver docstring ahí para el esquema de
cada tabla).

MAPEO CONTRA EL PDF — QUÉ SE REPLICA Y QUÉ NO
---------------------------------------------
Sí, con nuestra data:
  · Producción / "Inyección por área" y "por HUB"  -> serie["areas"]
  · "Detalle inyección por gasoducto" (apilado por área)
  · "Calidad de ingreso flujo a gasoducto" (PCS ponderado, si la tabla total
    trae una columna de PCS)
  · Tratamiento / "Ingreso a planta por área / gasoducto" -> serie["pool"]
  · "Procesado / BP" por planta (equivale al DP-TBX-BP del PDF: acá cada tren
    es una planta)
  · "Retenidos por compuesto [tn/d]" con los cortes C2/C3/C4/C5+
  · "PCS / IW entrada vs salida" por planta, con línea de máximo opcional
  · "Caudal vs capacidad" a nivel planta (gas disponible vs capacidad de
    ingreso)
  · La tabla resumen anual por planta (inyección, retenidos, PCS/IW in-out)

No (el modelo no lo produce): fuel gas, llenado y capacidad de los gasoductos
de EVACUACIÓN (CO Troncal/Paralelo, GPM, NEUII...) y el ruteo del gas residual
hacia esos ductos — la cascada termina en tratado/derivado/bypass, no asigna
destino aguas abajo. Si esos gráficos se vuelven prioritarios hay que agregar
esa capa al modelo, no a este tab.

UNIDADES: volúmenes en MMm3/d (Volumen_inyectado / FACTOR); LGN en tn/d;
PCS e IW en kcal/m3 con el IW calculado como PCS / sqrt(PM_mezcla / PM_aire).
"""

from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st

try:
    import altair as alt
except ImportError:
    alt = None


# Nomenclatura del PDF para los cortes de LGN.
CORTES = {"etano": "C2", "propano": "C3", "butanos": "C4", "gasolina": "C5+"}

ETIQUETAS_ORIGEN = {
    "yacimientos": "Inyección por área",
    "detalles_hubs": "Inyección por HUB",
    "flujos_directos": "Flujos directos (por gasoducto de origen)",
}

_EJE_T = None  # se instancia perezoso porque alt puede no estar


def _eje_tiempo():
    return alt.X("periodo:T", title=None, axis=alt.Axis(format="%m-%y", labelAngle=-45))


# ===========================================================================
# Helpers
# ===========================================================================

def _slug(texto) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(texto).lower()).strip("_")


def _descargar_csv(df: pd.DataFrame, nombre: str, key: str):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button(f"⬇️ {nombre}.csv", data=buf.getvalue(),
                       file_name=f"{nombre}.csv", mime="text/csv", key=key)


def _top_n_mas_otros(df: pd.DataFrame, col_cat: str, col_val: str,
                     top_n: int) -> pd.DataFrame:
    """Agrupa a (periodo, categoria) dejando las top-N por volumen total y
    fundiendo el resto en "Otros", igual que hace el dashboard del cliente.
    Sin esto, un apilado con 40 áreas es una franja ilegible de colores."""
    orden = (df.groupby(col_cat)[col_val].sum()
               .sort_values(ascending=False))
    top = set(orden.head(top_n).index)

    d = df[["periodo", col_cat, col_val]].copy()
    d[col_cat] = d[col_cat].where(d[col_cat].isin(top), "Otros")
    return d.groupby(["periodo", col_cat], as_index=False)[col_val].sum()


def _area_apilada(df, col_cat, col_val, y_titulo, fmt=",.2f", altura=360):
    # "Otros" al final de la pila y de la leyenda, como en el PDF.
    categorias = sorted(df[col_cat].unique(), key=lambda c: (c == "Otros", c))
    return (
        alt.Chart(df)
        .mark_area(opacity=0.85)
        .encode(
            x=_eje_tiempo(),
            y=alt.Y(f"{col_val}:Q", title=y_titulo, stack="zero"),
            color=alt.Color(f"{col_cat}:N", title=None, sort=categorias,
                            scale=alt.Scale(scheme="tableau20")),
            order=alt.Order(f"{col_cat}:N"),
            tooltip=[
                alt.Tooltip("periodo:T", format="%m-%Y", title="Período"),
                alt.Tooltip(f"{col_cat}:N", title=""),
                alt.Tooltip(f"{col_val}:Q", format=fmt, title=y_titulo),
            ],
        )
        .properties(height=altura)
        .interactive()
    )


def _lineas(df, col_serie, col_val, y_titulo, fmt=",.0f", altura=320,
            escala_desde_cero=False):
    y_scale = alt.Scale(zero=escala_desde_cero)
    return (
        alt.Chart(df)
        .mark_line()
        .encode(
            x=_eje_tiempo(),
            y=alt.Y(f"{col_val}:Q", title=y_titulo, scale=y_scale),
            color=alt.Color(f"{col_serie}:N", title=None),
            tooltip=[
                alt.Tooltip("periodo:T", format="%m-%Y", title="Período"),
                alt.Tooltip(f"{col_serie}:N", title=""),
                alt.Tooltip(f"{col_val}:Q", format=fmt, title=y_titulo),
            ],
        )
        .properties(height=altura)
        .interactive()
    )


def _regla_maximo(valor, texto):
    """Línea punteada de máximo, como los 'PCS MAX' / 'Límite' del PDF."""
    df = pd.DataFrame({"y": [valor], "etiqueta": [texto]})
    return alt.Chart(df).mark_rule(strokeDash=[6, 4], color="#1a1a1a").encode(
        y="y:Q", tooltip=[alt.Tooltip("etiqueta:N", title=""),
                          alt.Tooltip("y:Q", format=",.0f")])


# ===========================================================================
# Producción
# ===========================================================================

def _g_inyeccion(areas: pd.DataFrame):
    st.markdown("### Producción — inyección")

    origenes = [o for o in ETIQUETAS_ORIGEN if o in set(areas["origen"])]
    if not origenes:
        st.info("La serie no trae detalle por área.")
        return

    c1, c2 = st.columns([3, 1])
    origen = c1.radio("Vista", origenes, horizontal=True, key="g_iny_origen",
                      format_func=lambda o: ETIQUETAS_ORIGEN[o])
    top_n = c2.slider("Áreas a mostrar", 4, 20, 10, key="g_iny_topn",
                      help="El resto se agrupa en 'Otros', como en el Excel.")

    df = areas[(areas["origen"] == origen) & areas["volumen"].notna()]
    if df.empty:
        st.info("Sin filas para esta vista.")
        return

    apilado = _top_n_mas_otros(df, "area", "volumen", top_n)
    st.altair_chart(_area_apilada(apilado, "area", "volumen", "MMm3/d"),
                    use_container_width=True)


def _g_detalle_gasoducto(areas: pd.DataFrame):
    st.markdown("### Detalle inyección por gasoducto")
    st.caption("Composición del caudal de cada gasoducto/destino, abierto por "
               "área de origen (equivale a las láminas VMN / VMS / NEUI / ... "
               "del Excel).")

    df = areas[areas["gasoducto"].notna() & areas["volumen"].notna()]
    if df.empty:
        st.info("La serie no trae la columna `Gasoducto`.")
        return

    # Ordenados por volumen total para que el default sea el gasoducto gordo.
    orden = (df.groupby("gasoducto")["volumen"].sum()
               .sort_values(ascending=False).index.tolist())
    gasoducto = st.selectbox("Gasoducto / destino", orden, key="g_gas_sel")

    apilado = _top_n_mas_otros(df[df["gasoducto"] == gasoducto],
                               "area", "volumen", top_n=10)
    st.altair_chart(_area_apilada(apilado, "area", "volumen", "MMm3/d"),
                    use_container_width=True)


def _g_calidad_gasoducto(areas: pd.DataFrame):
    if "pcs" not in areas.columns or areas["pcs"].notna().sum() == 0:
        st.caption("ℹ️ Las tablas totales no traen columna de PCS por fila: "
                   "se omite «Calidad de ingreso por gasoducto».")
        return

    st.markdown("### Calidad de ingreso flujo a gasoducto")
    st.caption("PCS del gas de cada gasoducto, ponderado por volumen de cada "
               "área que le inyecta.")

    df = areas[areas["gasoducto"].notna()
               & areas["pcs"].notna() & areas["volumen"].notna()].copy()
    df["pcs_x_vol"] = df["pcs"] * df["volumen"]
    pond = df.groupby(["periodo", "gasoducto"], as_index=False).agg(
        pcs_x_vol=("pcs_x_vol", "sum"), volumen=("volumen", "sum"))
    pond = pond[pond["volumen"] > 0]
    pond["valor"] = pond["pcs_x_vol"] / pond["volumen"]

    todos = sorted(pond["gasoducto"].unique())
    sel = st.multiselect("Gasoductos", todos, default=todos[:4], key="g_cal_sel")
    if not sel:
        return
    st.altair_chart(
        _lineas(pond[pond["gasoducto"].isin(sel)].rename(columns={"gasoducto": "serie"}),
                "serie", "valor", "PCS [kcal/m3]"),
        use_container_width=True)


# ===========================================================================
# Tratamiento
# ===========================================================================

def _g_ingreso_planta(pool: pd.DataFrame):
    st.markdown("### Ingreso a planta por área / gasoducto")
    st.caption("El pool de la planta abierto por origen. `Pool` es el gas "
               "antes del reparto; `Asignado`, la porción que la planta "
               "efectivamente trata.")

    if pool.empty:
        st.info("La serie no trae el detalle del pool por planta.")
        return

    c1, c2 = st.columns([2, 2])
    plantas = sorted(pool["planta"].unique())
    planta = c1.selectbox("Planta", plantas,
                          index=plantas.index("MEGA") if "MEGA" in plantas else 0,
                          key="g_pool_planta")
    medida = c2.radio("Medida", ["Pool", "Asignado"], horizontal=True,
                      key="g_pool_medida")
    col_val = "vol_pool" if medida == "Pool" else "vol_asignado"

    df = pool[(pool["planta"] == planta) & pool[col_val].notna()]
    if df.empty:
        st.info("Sin filas para esta planta.")
        return
    apilado = _top_n_mas_otros(df, "area", col_val, top_n=10)
    st.altair_chart(_area_apilada(apilado, "area", col_val, "MMm3/d"),
                    use_container_width=True)


def _g_procesado_bp(plantas_df: pd.DataFrame):
    st.markdown("### Procesado y ByPass del pool")
    st.caption("Equivalente al «Procesado / BP» por planta del Excel: acá "
               "TBX y DP son eslabones separados sobre el mismo pool. El "
               "`vol_derivado` no se apila porque ya está contado como "
               "disponible del eslabón siguiente.")

    partes = []
    for _, fila in plantas_df.iterrows():
        partes.append({"periodo": fila["periodo"],
                       "serie": f"{fila['planta']} Procesado",
                       "valor": fila["vol_asignado"]})
        partes.append({"periodo": fila["periodo"],
                       "serie": f"{fila['planta']} BP",
                       "valor": fila["bypass"]})
    largo = pd.DataFrame(partes).dropna(subset=["valor"])
    largo = largo[largo["valor"].abs() > 1e-12]

    if largo.empty:
        st.info("Sin volúmenes para graficar.")
        return
    st.altair_chart(_area_apilada(largo, "serie", "valor", "MMm3/d"),
                    use_container_width=True)


def _g_retenidos(plantas_df: pd.DataFrame):
    st.markdown("### Retenidos por compuesto [tn/d]")

    cols = {f"lgn_{k}": v for k, v in CORTES.items()
            if f"lgn_{k}" in plantas_df.columns}
    if not cols:
        st.info("La serie no trae el desglose por corte.")
        return

    opciones = ["Todas las plantas"] + sorted(plantas_df["planta"].unique())
    sel = st.selectbox("Planta", opciones, key="g_ret_planta")

    df = plantas_df if sel == "Todas las plantas" else plantas_df[plantas_df["planta"] == sel]
    largo = (df.groupby("periodo", as_index=False)[list(cols)].sum()
               .melt(id_vars="periodo", var_name="serie", value_name="valor"))
    largo["serie"] = largo["serie"].map(cols)

    st.altair_chart(
        _area_apilada(largo, "serie", "valor", "tn/d", fmt=",.1f", altura=320),
        use_container_width=True)


def _g_pcs_iw(plantas_df: pd.DataFrame):
    if plantas_df["pcs_in"].notna().sum() == 0:
        st.caption("ℹ️ No se pudo calcular PCS/IW (la hoja `propiedades` no "
                   "trae una columna de PCS por compuesto): se omite «Calidad "
                   "por planta».")
        return

    st.markdown("### Calidad por planta — PCS e Índice de Wobbe")
    st.caption("Entrada = mezcla del gas rico del pool; salida = gas residual "
               "normalizado. La línea punteada es el máximo de referencia "
               "(0 = no mostrar), como los `PCS MAX` / `IW MAX` del Excel.")

    c1, c2, c3 = st.columns([2, 1, 1])
    plantas = sorted(plantas_df["planta"].unique())
    planta = c1.selectbox("Planta", plantas,
                          index=plantas.index("MEGA") if "MEGA" in plantas else 0,
                          key="g_pcs_planta")
    pcs_max = c2.number_input("PCS MAX [kcal/m3]", value=0.0, step=100.0,
                              key="g_pcs_max")
    iw_max = c3.number_input("IW MAX [kcal/m3]", value=0.0, step=100.0,
                             key="g_iw_max")

    df = plantas_df[plantas_df["planta"] == planta]

    def _panel(col_in, col_out, titulo, maximo):
        largo = df.melt(id_vars="periodo", value_vars=[col_in, col_out],
                        var_name="serie", value_name="valor").dropna(subset=["valor"])
        largo["serie"] = largo["serie"].map(
            {col_in: "Ingreso", col_out: "Salida"})
        if largo.empty:
            st.info(f"Sin datos de {titulo}.")
            return
        grafico = _lineas(largo, "serie", "valor", f"{titulo} [kcal/m3]")
        if maximo and maximo > 0:
            grafico = grafico + _regla_maximo(maximo, f"{titulo} MAX")
        st.altair_chart(grafico, use_container_width=True)

    izq, der = st.columns(2)
    with izq:
        st.markdown(f"**{planta} — PCS**")
        _panel("pcs_in", "pcs_out", "PCS", pcs_max)
    with der:
        st.markdown(f"**{planta} — IW**")
        if df["iw_in"].notna().sum() == 0:
            st.info("Sin peso molecular en `propiedades`: no se puede "
                    "calcular el IW.")
        else:
            _panel("iw_in", "iw_out", "IW", iw_max)


def _g_caudal_capacidad(plantas_df: pd.DataFrame):
    st.markdown("### Caudal vs. capacidad (por planta)")
    st.caption("Versión planta del «Inyección total vs capacidad de tpe» del "
               "Excel: gas disponible del eslabón contra su capacidad de "
               "ingreso. La capacidad de los gasoductos de evacuación no está "
               "en el modelo.")

    plantas = sorted(plantas_df["planta"].unique())
    planta = st.selectbox("Planta", plantas, key="g_cap_planta")
    df = plantas_df[plantas_df["planta"] == planta]

    area = (
        alt.Chart(df.dropna(subset=["vol_disponible"]))
        .mark_area(opacity=0.7, color="#2E86C1")
        .encode(x=_eje_tiempo(),
                y=alt.Y("vol_disponible:Q", title="MMm3/d"),
                tooltip=[alt.Tooltip("periodo:T", format="%m-%Y", title="Período"),
                         alt.Tooltip("vol_disponible:Q", format=",.2f",
                                     title="Caudal disponible")])
    )
    capas = area
    if df["capacidad_ingreso"].notna().sum() > 0:
        linea = (
            alt.Chart(df.dropna(subset=["capacidad_ingreso"]))
            .mark_line(color="#E67E22", strokeWidth=3)
            .encode(x=_eje_tiempo(), y="capacidad_ingreso:Q",
                    tooltip=[alt.Tooltip("capacidad_ingreso:Q", format=",.2f",
                                         title="Capacidad de ingreso")])
        )
        capas = area + linea
    st.altair_chart(capas.properties(height=320).interactive(),
                    use_container_width=True)


# ===========================================================================
# Resumen anual (la tablita del PDF)
# ===========================================================================

_FILAS_RESUMEN = {
    "Gas tratado [MMm3/d]": ("vol_asignado", "{:,.2f}"),
    "Retenidos [tn/d]": ("lgn_asignado", "{:,.0f}"),
    "PCS entrada [kcal/m3]": ("pcs_in", "{:,.0f}"),
    "PCS salida [kcal/m3]": ("pcs_out", "{:,.0f}"),
    "IW entrada [kcal/m3]": ("iw_in", "{:,.0f}"),
    "IW salida [kcal/m3]": ("iw_out", "{:,.0f}"),
}


def _tabla_resumen_anual(plantas_df: pd.DataFrame):
    st.markdown("### Resumen anual por planta")
    st.caption("Promedio simple de los meses calculados de cada año.")

    d = plantas_df.copy()
    d["año"] = pd.to_datetime(d["periodo"]).dt.year

    agg = d.groupby(["planta", "año"]).agg(
        **{fila: (col, "mean") for fila, (col, _) in _FILAS_RESUMEN.items()})

    crudo = agg.T  # filas = métricas, columnas = (planta, año)
    crudo.columns = [f"{p} {a}" for p, a in crudo.columns]

    vista = crudo.copy()
    for fila, (_, formato) in _FILAS_RESUMEN.items():
        vista.loc[fila] = crudo.loc[fila].map(
            lambda v: "—" if pd.isna(v) else formato.format(v))

    st.dataframe(vista, use_container_width=True)
    _descargar_csv(crudo.reset_index(names="Métrica"), "resumen_anual",
                   key="dl_resumen_anual")


# ===========================================================================
# Fallback sin serie
# ===========================================================================

def _sin_serie(resultados: dict):
    st.info(
        "Todavía no hay serie temporal calculada. Cargá el rango en la barra "
        "lateral (**7. Serie temporal**) y apretá **📈 Calcular serie**: los "
        "gráficos del dashboard salen de ahí."
    )

    plantas = (resultados or {}).get("plantas", {})
    if not plantas:
        return

    st.markdown("**Retenidos del período actual [tn/d]**")
    filas = []
    for planta, datos in plantas.items():
        rv = datos.get("retenidos_vol")
        if not isinstance(rv, pd.DataFrame):
            continue
        fila = {"Planta": planta}
        for corte, etiqueta in CORTES.items():
            if corte in rv.columns:
                fila[etiqueta] = float(pd.to_numeric(
                    rv[corte], errors="coerce").fillna(0).sum())
        filas.append(fila)
    if filas:
        st.dataframe(pd.DataFrame(filas), use_container_width=True)


# ===========================================================================
# Panel
# ===========================================================================

def panel_graphs(resultados: dict, serie: dict | None = None,
                 fallos: list | None = None):
    if alt is None:
        st.error("Falta `altair`. Instalalo con `pip install altair`.")
        return

    if fallos:
        with st.expander(f"⚠️ {len(fallos)} período(s) fallaron al calcular la serie"):
            for periodo, error in fallos:
                st.write(f"- **{pd.Timestamp(periodo).strftime('%m-%Y')}**: {error}")

    if not serie or not isinstance(serie, dict) or serie.get("plantas") is None \
            or len(serie["plantas"]) == 0:
        _sin_serie(resultados)
        return

    plantas_df = serie["plantas"].copy()
    plantas_df["periodo"] = pd.to_datetime(plantas_df["periodo"])
    areas = serie.get("areas", pd.DataFrame()).copy()
    pool = serie.get("pool", pd.DataFrame()).copy()
    for df in (areas, pool):
        if "periodo" in df.columns:
            df["periodo"] = pd.to_datetime(df["periodo"])

    if plantas_df["periodo"].nunique() == 1:
        st.warning("La serie tiene un solo período: los gráficos van a "
                   "mostrar una sola columna. Ampliá el rango en la barra "
                   "lateral.")

    # --- Producción ------------------------------------------------------
    if not areas.empty:
        _g_inyeccion(areas)
        st.divider()
        _g_detalle_gasoducto(areas)
        st.divider()
        _g_calidad_gasoducto(areas)
        st.divider()

    # --- Tratamiento ------------------------------------------------------
    _g_ingreso_planta(pool)
    st.divider()
    _g_procesado_bp(plantas_df)
    st.divider()
    _g_retenidos(plantas_df)
    st.divider()
    _g_pcs_iw(plantas_df)
    st.divider()
    _g_caudal_capacidad(plantas_df)
    st.divider()

    # --- Resumen y descargas ---------------------------------------------
    _tabla_resumen_anual(plantas_df)

    with st.expander("Descargar los datos de la serie"):
        c1, c2, c3 = st.columns(3)
        with c1:
            _descargar_csv(plantas_df, "serie_plantas", key="dl_sp")
        with c2:
            if not areas.empty:
                _descargar_csv(areas, "serie_areas", key="dl_sa")
        with c3:
            if not pool.empty:
                _descargar_csv(pool, "serie_pool", key="dl_spool")
