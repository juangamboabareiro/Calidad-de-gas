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
    st.download_button(f"{nombre}.csv", data=buf.getvalue(),
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
# Lámina objetivo: inyección a transporte, calidad de la mezcla y PRHC
# ===========================================================================

def _g_inyeccion_tpe(mezcla: pd.DataFrame):
    st.markdown("### Inyección a sistema de tpe [MMm3/d STD]")
    st.caption(
        "Salida de plantas (residual) + gas que va directo al sistema. En el "
        "modelo, «Directo a gasoducto» es el bypass de la cascada: gas del "
        "pool que ninguna planta trató. La inyección de áreas que nunca pasan "
        "por un pool no está clasificada como transporte vs. alimentación de "
        "planta, así que no se incluye acá."
    )

    cols_vol = [c for c in mezcla.columns if c.startswith("vol_")]
    if not cols_vol:
        st.info("La serie no trae los volúmenes de la mezcla.")
        return

    etiquetas = {"vol_mega": "MEGA", "vol_tty": "TTY",
                 "vol_directo_a_gasoducto": "Directo a gasoducto"}
    largo = mezcla.melt(id_vars="periodo", value_vars=cols_vol,
                        var_name="serie", value_name="valor").dropna(subset=["valor"])
    largo["serie"] = largo["serie"].map(
        lambda c: etiquetas.get(c, c.removeprefix("vol_").replace("_", " ")))

    # Orden de la lámina: MEGA abajo, TTY al medio, directo arriba.
    orden = ["MEGA", "TTY", "Directo a gasoducto"]
    orden += [s for s in largo["serie"].unique() if s not in orden]
    colores = alt.Scale(domain=orden,
                        range=["#1F3B5C", "#2E86C1", "#5D8233"][:len(orden)]) \
        if set(largo["serie"]) <= set(orden[:3]) else alt.Undefined

    grafico = (
        alt.Chart(largo)
        .mark_area(opacity=0.9)
        .encode(
            x=_eje_tiempo(),
            y=alt.Y("valor:Q", title="MMm3/d", stack="zero"),
            color=alt.Color("serie:N", title=None, sort=orden, scale=colores),
            order=alt.Order("serie:N"),
            tooltip=[alt.Tooltip("periodo:T", format="%m-%Y", title="Período"),
                     alt.Tooltip("serie:N", title=""),
                     alt.Tooltip("valor:Q", format=",.2f", title="MMm3/d")],
        )
        .properties(height=340)
        .interactive()
    )
    st.altair_chart(grafico, use_container_width=True)


def _g_calidad_mezcla(mezcla: pd.DataFrame):
    # `in columns` primero: una serie armada con meses sin mezcla_transporte
    # no trae la columna y el acceso directo tiraria KeyError, matando ademas
    # todos los tabs que se renderizan despues de Graphs.
    if "pcs" not in mezcla.columns or mezcla["pcs"].notna().sum() == 0:
        st.caption("ℹ️ Sin PCS por compuesto en `propiedades`: se omite "
                   "«Calidad del gas» de la mezcla.")
        return

    st.markdown("### Calidad del gas [kcal/m3]")
    st.caption("PCS e Índice de Wobbe de la mezcla total inyectada a "
               "transporte (salidas de planta + bypass, ponderada por "
               "volumen; el IW se calcula sobre la composición mezclada, no "
               "promediando IWs).")

    c1, c2 = st.columns(2)
    pcs_max = c1.number_input("PCS MAX [kcal/m3]", value=10_700.0, step=50.0,
                              key="g_mez_pcs_max",
                              help="0 = no mostrar la línea.")
    iw_max = c2.number_input("IW MAX [kcal/m3]", value=13_000.0, step=50.0,
                             key="g_mez_iw_max", help="0 = no mostrar la línea.")

    largo = mezcla.melt(id_vars="periodo",
                        value_vars=[c for c in ("pcs", "iw") if c in mezcla.columns],
                        var_name="serie", value_name="valor").dropna(subset=["valor"])
    largo["serie"] = largo["serie"].map({"pcs": "PCS [kcal/m3]", "iw": "IW"})

    colores = alt.Scale(domain=["PCS [kcal/m3]", "IW"],
                        range=["#1E8449", "#9B59B6"])
    grafico = (
        alt.Chart(largo)
        .mark_line(strokeWidth=2.5)
        .encode(
            x=_eje_tiempo(),
            y=alt.Y("valor:Q", title="kcal/m3", scale=alt.Scale(zero=False)),
            color=alt.Color("serie:N", title=None, scale=colores),
            tooltip=[alt.Tooltip("periodo:T", format="%m-%Y", title="Período"),
                     alt.Tooltip("serie:N", title=""),
                     alt.Tooltip("valor:Q", format=",.0f", title="kcal/m3")],
        )
        .properties(height=340)
    )
    if pcs_max > 0:
        grafico = grafico + _regla_maximo(pcs_max, "PCS MAX")
    if iw_max > 0:
        grafico = grafico + _regla_maximo(iw_max, "IW MAX")
    st.altair_chart(grafico.interactive(), use_container_width=True)


def _leer_prhc_externo(archivo) -> pd.DataFrame | None:
    """Lee la salida del software de PRHC: busca una columna de fecha y una de
    PRHC por nombre (regex), sin exigir un formato exacto de encabezados."""
    try:
        if archivo.name.lower().endswith((".xlsx", ".xls", ".xlsm")):
            ext = pd.read_excel(archivo)
        else:
            ext = pd.read_csv(archivo, sep=None, engine="python")
    except Exception as e:
        st.error(f"No pude leer el archivo: {e}")
        return None

    col_fecha = next((c for c in ext.columns
                      if re.search(r"period|fecha|mes|date", str(c), re.I)), None)
    col_prhc = next((c for c in ext.columns
                     if re.search(r"prhc|roc[ií]o|dew", str(c), re.I)), None)
    if col_fecha is None or col_prhc is None:
        st.error("El archivo necesita una columna de período (`periodo`, "
                 "`fecha`, `mes`...) y una de PRHC (`prhc`, `rocío`, "
                 f"`dewpoint`...). Encontré: {list(ext.columns)}")
        return None

    df = pd.DataFrame({
        "periodo": pd.to_datetime(ext[col_fecha], errors="coerce"),
        "valor": pd.to_numeric(ext[col_prhc], errors="coerce"),
    })
    if df["periodo"].isna().all():
        # Segundo intento para formatos tipo "07-2026" / "01/07/2026".
        df["periodo"] = pd.to_datetime(ext[col_fecha].astype(str),
                                       errors="coerce", dayfirst=True)
    df = df.dropna()
    if df.empty:
        st.error("No quedó ninguna fila válida después de parsear fechas y "
                 "valores.")
        return None
    df["periodo"] = df["periodo"].dt.to_period("M").dt.to_timestamp()
    return df


def _g_prhc(mezcla: pd.DataFrame):
    st.markdown("### PRHC de la mezcla [°C]")

    # Fuente 1: hook interno (domain/prhc.py), si algún día existe.
    df = None
    if "prhc" in mezcla.columns and mezcla["prhc"].notna().sum() > 0:
        df = mezcla.dropna(subset=["prhc"])[["periodo", "prhc"]].rename(
            columns={"prhc": "valor"})
        st.caption("Calculado con `domain/prhc.py` sobre la composición de "
                   "la mezcla.")
    else:
        # Fuente 2 (la habitual): la salida del software externo de PRHC.
        st.caption("El PRHC se calcula en otro software: subí acá su salida "
                   "(una columna de período y una de PRHC en °C) y se "
                   "grafica contra el límite.")
        archivo = st.file_uploader(
            "CSV o Excel con el PRHC por período", type=["csv", "xlsx", "xls", "xlsm"],
            key="g_prhc_upload")
        if archivo is None:
            return
        df = _leer_prhc_externo(archivo)
        if df is None:
            return

    limite = st.number_input("Límite PRHC [°C]", value=-4.0, step=0.5,
                             key="g_prhc_limite")

    df = df.sort_values("periodo").copy()
    df["serie"] = "Mezcla con salida de plantas"
    grafico = _lineas(df, "serie", "valor", "°C", fmt=",.1f")
    grafico = grafico + _regla_maximo(limite, f"Límite PRHC {limite:g}°C")
    st.altair_chart(grafico, use_container_width=True)

    excesos = df[df["valor"] > limite]
    if len(excesos):
        primero = excesos["periodo"].min()
        st.warning(f"⚠️ La mezcla supera el límite de {limite:g}°C en "
                   f"{len(excesos)} período(s), desde "
                   f"{pd.Timestamp(primero).strftime('%m-%Y')}.")


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
    if "pcs_in" not in plantas_df.columns or plantas_df["pcs_in"].notna().sum() == 0:
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
        if "iw_in" not in df.columns or df["iw_in"].notna().sum() == 0:
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

    # Solo las metricas cuya columna existe: una serie generada por una
    # version intermedia del pipeline puede no traer pcs/iw y el agg con
    # labels inexistentes tira KeyError (y mata los tabs posteriores).
    filas_disponibles = {fila: (col, fmt) for fila, (col, fmt)
                         in _FILAS_RESUMEN.items() if col in d.columns}
    if not filas_disponibles:
        st.info("La serie no trae las métricas del resumen.")
        return

    agg = d.groupby(["planta", "año"]).agg(
        **{fila: (col, "mean") for fila, (col, _) in filas_disponibles.items()})

    crudo = agg.T  # filas = métricas, columnas = (planta, año)
    crudo.columns = [f"{p} {a}" for p, a in crudo.columns]

    # La vista formateada se arma como tabla de strings desde el vacio. Antes
    # se hacia `vista.loc[fila] = strings` sobre una copia float: pandas >= 3
    # ya no convierte el dtype en la asignacion y revienta con
    # "Invalid value for dtype 'float64'".
    vista = pd.DataFrame(
        {
            fila: crudo.loc[fila].map(
                lambda v, f=formato: "—" if pd.isna(v) else f.format(v))
            for fila, (_, formato) in filas_disponibles.items()
        }
    ).T
    vista.columns = crudo.columns

    st.dataframe(vista, use_container_width=True)
    _descargar_csv(crudo.reset_index(names="Métrica"), "resumen_anual",
                   key="dl_resumen_anual")


# ===========================================================================
# Fallback sin serie
# ===========================================================================

def _sin_serie(resultados: dict):
    st.info(
        "Todavía no hay serie temporal calculada. Cargá el rango en la barra "
        "lateral (**7. Serie temporal**) y apretá **Calcular serie**: los "
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

_REF_9300 = 9_300.0
_UNIDAD_9300 = "MMm³/d de 9.300 kcal"
_UNIDAD_STD = "MMm³/d STD"

# Columnas volumétricas de serie["plantas"] que se expresan en la unidad
# elegida. Todas son gas del pool (rico), asi que convierten con pcs_in;
# la capacidad de ingreso tambien, para que el grafico caudal-vs-capacidad
# compare peras con peras en terminos de energia.
_COLS_VOL_PLANTAS = ("vol_disponible", "vol_maximo", "vol_asignado",
                     "sobrante", "vol_derivado", "bypass", "capacidad_ingreso")


def _serie_en_9300(serie: dict):
    """Convierte los volúmenes de la serie de STD a equivalentes de 9.300 kcal.

    V_9300 = V_STD x PCS / 9300, con el PCS PROPIO de cada corriente: un gas
    mas rico rinde mas metros equivalentes. Fuentes del PCS: filas de areas ->
    su columna pcs; plantas -> pcs_in (todo lo convertido es gas del pool);
    pool -> el pcs_in de su (planta, periodo); mezcla -> el pcs de la mezcla.

    Filas sin PCS quedan en NaN (fuera de la vista) en vez de mezclarse en
    otra unidad sin avisar. Devuelve (serie_convertida, avisos).
    """
    avisos = []
    plantas = serie.get("plantas", pd.DataFrame()).copy()
    areas = serie.get("areas", pd.DataFrame()).copy()
    pool = serie.get("pool", pd.DataFrame()).copy()
    mezcla = serie.get("mezcla", pd.DataFrame()).copy()

    # La conversion necesita PCS en kcal/m3. Si Constantes-GAS vino con
    # Conversion=1000, los PCS estan en MJ/m3 (~40) y dividir por 9300 daria
    # basura: mejor no convertir y decirlo.
    pcs_ref = pd.concat([plantas.get("pcs_in", pd.Series(dtype=float)),
                         areas.get("pcs", pd.Series(dtype=float))]).dropna()
    if len(pcs_ref) and pcs_ref.median() < 1000:
        return serie, ["Los PCS de la serie no están en kcal/m³ (¿Conversion="
                       "1000 en Constantes-GAS?): se muestra en STD."]

    if len(plantas):
        if "pcs_in" in plantas.columns and plantas["pcs_in"].notna().any():
            factor = plantas["pcs_in"] / _REF_9300
            for col in _COLS_VOL_PLANTAS:
                if col in plantas.columns:
                    plantas[col] = plantas[col] * factor
            sin = int(plantas["pcs_in"].isna().sum())
            if sin:
                avisos.append(f"{sin} fila(s) de plantas sin PCS quedaron "
                              "fuera de la vista 9.300.")
        else:
            avisos.append("La serie de plantas no trae pcs_in: sus volúmenes "
                          "quedaron fuera de la vista 9.300 (recalculá la serie).")
            for col in _COLS_VOL_PLANTAS:
                if col in plantas.columns:
                    plantas[col] = pd.NA

    if len(areas) and "volumen" in areas.columns:
        if "pcs" in areas.columns:
            # La mascara va ANTES de convertir: despues, el volumen de las
            # filas sin PCS ya es NaN y el contador daria siempre cero.
            sin_pcs = areas["pcs"].isna() & areas["volumen"].notna()
            areas["volumen"] = areas["volumen"] * areas["pcs"] / _REF_9300
            if sin_pcs.any():
                avisos.append(f"{int(sin_pcs.sum())} fila(s) de áreas sin PCS "
                              "quedaron fuera de la vista 9.300.")
        else:
            areas["volumen"] = pd.NA

    if len(pool) and len(plantas) and "pcs_in" in plantas.columns:
        # El pool convierte con el pcs_in de su planta en ese periodo.
        clave = plantas[["periodo", "planta", "pcs_in"]].drop_duplicates()
        pool = pool.merge(clave, on=["periodo", "planta"], how="left")
        for col in ("vol_pool", "vol_asignado"):
            if col in pool.columns:
                pool[col] = pool[col] * pool["pcs_in"] / _REF_9300
        pool = pool.drop(columns=["pcs_in"])

    if len(mezcla) and "pcs" in mezcla.columns:
        f = mezcla["pcs"] / _REF_9300
        for col in ("vol_mega", "vol_tty", "vol_directo_a_gasoducto"):
            if col in mezcla.columns:
                mezcla[col] = mezcla[col] * f

    return ({"plantas": plantas, "areas": areas, "pool": pool,
             "mezcla": mezcla}, avisos)


def _selector_unidad(serie: dict, unidad: str | None = None):
    """Convierte la serie a la unidad global elegida en la sidebar.

    `unidad` llega desde app.py (el selector es uno solo para toda la app);
    None equivale a STD para no romper llamadas viejas.
    """
    if unidad != _UNIDAD_9300:
        st.caption(f"Todos los volúmenes expresados en **{_UNIDAD_STD}**.")
        return serie, _UNIDAD_STD
    convertida, avisos = _serie_en_9300(serie)
    for aviso in avisos:
        st.warning(aviso)
    etiqueta = _UNIDAD_STD if convertida is serie else _UNIDAD_9300
    st.caption(f"Todos los volúmenes expresados en **{etiqueta}**.")
    return convertida, etiqueta


def panel_graphs(resultados: dict, serie: dict | None = None,
                 fallos: list | None = None, unidad: str | None = None):
    if alt is None:
        st.error("Falta `altair`. Instalalo con `pip install altair`.")
        return

    if fallos:
        with st.expander(f"⚠️ {len(fallos)} período(s) fallaron al calcular la serie"):
            for periodo, error in fallos:
                st.write(f"- **{pd.Timestamp(periodo).strftime('%m-%Y')}**: {error}")

    # `isinstance` va PRIMERO: si en session_state quedo una serie del formato
    # viejo (un DataFrame suelto), `not serie` revienta con "truth value of a
    # DataFrame is ambiguous" antes de llegar al chequeo de tipo. Con el orden
    # correcto, una serie vieja simplemente muestra el aviso de recalcular.
    if not isinstance(serie, dict) or serie.get("plantas") is None \
            or len(serie["plantas"]) == 0:
        _sin_serie(resultados)
        return

    serie, unidad_volumen = _selector_unidad(serie, unidad)

    plantas_df = serie["plantas"].copy()
    plantas_df["periodo"] = pd.to_datetime(plantas_df["periodo"])

    # --- Reporte PDF: la misma serie, en láminas para imprimir/enviar ------
    # Se genera bajo demanda (matplotlib redibuja todo) y el resultado queda
    # en session_state para que el download_button sobreviva a los reruns.
    c_rep1, c_rep2 = st.columns([1, 1])
    if c_rep1.button("Generar reporte PDF", key="btn_reporte_pdf",
                     help="Arma las láminas del dashboard con la serie actual."):
        try:
            from ui.reporte_graphs import generar_reporte_pdf
            with st.spinner("Armando el reporte..."):
                st.session_state["reporte_pdf"] = generar_reporte_pdf(
                    serie, unidad_label=unidad_volumen)
        except ImportError:
            st.error("Falta `matplotlib` para el reporte: agregalo a "
                     "requirements.txt (`matplotlib`) y redeployá.")
        except Exception as e:  # noqa: BLE001 - el reporte no puede tumbar el tab
            st.error(f"No se pudo generar el reporte: {e}")
    if st.session_state.get("reporte_pdf"):
        c_rep2.download_button(
            "Descargar reporte_graphs.pdf",
            data=st.session_state["reporte_pdf"],
            file_name=f"reporte_graphs_{pd.Timestamp.now():%Y%m%d}.pdf",
            mime="application/pdf", key="dl_reporte_pdf")

    with st.expander("Exportar gráficos sueltos (para presentaciones)"):
        try:
            from ui.reporte_graphs import catalogo_graficos, exportar_graficos
            _catalogo = list(catalogo_graficos(serie))
        except ImportError:
            st.error("Falta `matplotlib` para exportar: agregalo a "
                     "requirements.txt y redeployá.")
            _catalogo = []

        if _catalogo:
            st.caption(
                "Elegí los gráficos y ajustá tamaño y calidad de cada uno. El "
                "default (25.4 × 14.3 cm) es el cuerpo de una slide 16:9; el "
                "DPI define la nitidez del PNG (200 alcanza para proyectar, "
                "300 para imprimir).")
            sel = st.multiselect("Gráficos a exportar", _catalogo,
                                 default=_catalogo[:2], key="exp_sel")
            formato = st.radio(
                "Formato", ["PNG", "SVG"], horizontal=True, key="exp_fmt",
                help="PNG se pega directo en la slide. SVG es vectorial: se ve "
                     "perfecto a cualquier zoom y es editable en PowerPoint; "
                     "el DPI no le aplica.")
            if sel:
                semilla = pd.DataFrame({"Gráfico": sel,
                                        "Ancho [cm]": 25.4,
                                        "Alto [cm]": 14.3,
                                        "DPI": 200})
                # La key depende de la selección: al cambiarla, la tabla se
                # resiembra (si no, el editor arrastra filas de la selección
                # anterior).
                tabla = st.data_editor(
                    semilla, hide_index=True, use_container_width=True,
                    key=f"exp_editor_{abs(hash(tuple(sel))) % 99991}",
                    column_config={
                        "Gráfico": st.column_config.TextColumn(disabled=True),
                        "Ancho [cm]": st.column_config.NumberColumn(
                            min_value=5.0, max_value=60.0, step=0.5),
                        "Alto [cm]": st.column_config.NumberColumn(
                            min_value=4.0, max_value=40.0, step=0.5),
                        "DPI": st.column_config.NumberColumn(
                            min_value=72, max_value=600, step=10),
                    })
                if st.button("Generar", key="btn_exportar_graficos"):
                    pedidos = [{"nombre": f["Gráfico"],
                                "ancho_cm": f["Ancho [cm]"],
                                "alto_cm": f["Alto [cm]"],
                                "dpi": f["DPI"]}
                               for _, f in tabla.iterrows()]
                    try:
                        with st.spinner("Renderizando..."):
                            st.session_state["export_graficos"] = exportar_graficos(
                                serie, pedidos, formato=formato.lower())
                    except Exception as e:  # noqa: BLE001
                        st.error(f"No se pudo exportar: {e}")
                if st.session_state.get("export_graficos"):
                    contenido, nombre, mime = st.session_state["export_graficos"]
                    st.download_button(f"Descargar {nombre}", data=contenido,
                                       file_name=nombre, mime=mime,
                                       key="dl_export_graficos")
    st.divider()
    areas = serie.get("areas", pd.DataFrame()).copy()
    pool = serie.get("pool", pd.DataFrame()).copy()
    mezcla = serie.get("mezcla", pd.DataFrame()).copy()
    for df in (areas, pool, mezcla):
        if "periodo" in df.columns:
            df["periodo"] = pd.to_datetime(df["periodo"])

    if plantas_df["periodo"].nunique() == 1:
        st.warning("La serie tiene un solo período: los gráficos van a "
                   "mostrar una sola columna. Ampliá el rango en la barra "
                   "lateral.")

    # --- Lámina objetivo: sistema de transporte ---------------------------
    if not mezcla.empty:
        _g_inyeccion_tpe(mezcla)
        st.divider()
        _g_calidad_mezcla(mezcla)
        st.divider()
        _g_prhc(mezcla)
        st.divider()

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
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _descargar_csv(plantas_df, "serie_plantas", key="dl_sp")
        with c2:
            if not areas.empty:
                _descargar_csv(areas, "serie_areas", key="dl_sa")
        with c3:
            if not pool.empty:
                _descargar_csv(pool, "serie_pool", key="dl_spool")
        with c4:
            if not mezcla.empty:
                _descargar_csv(mezcla, "serie_mezcla", key="dl_smez")
