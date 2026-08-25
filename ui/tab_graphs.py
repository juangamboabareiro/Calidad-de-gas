"""
Tab "Graphs" — KPIs y series temporales.
========================================

Uso desde app.py:

    from ui.tab_graphs import panel_graphs
    ...
    with tab_graphs:
        panel_graphs(resultados, serie=st.session_state.get("serie"), factor_mm=FACTOR_MM)

QUE ES `serie`
--------------
Un DataFrame LARGO con una fila por (periodo, planta), armado en app.py por
`ejecutar_serie()`, que corre el pipeline una vez por mes. Columnas:

    periodo, planta, activa
    vol_disponible, vol_asignado, vol_maximo, vol_derivado, bypass, sobrante   [MMm3/d]
    lgn_asignado, capacidad_evacuacion                                          [tn/d]
    ocupacion                                                                   [%]
    lgn_por_mmm3                                       [tn LGN / MMm3 de gas]
    lgn_etano / lgn_propano / lgn_butanos / lgn_gasolina                        [tn/d]
    x_<compuesto>          fraccion molar del GAS RESIDUAL de esa planta

Si `serie` viene vacio o None el panel no queda en blanco: muestra los KPI y la
composicion del periodo que ya esta cargado en `resultados` y explica como
calcular la serie.

BASE DE LA COMPOSICION — LEER ANTES DE INTERPRETAR EL GRAFICO
-------------------------------------------------------------
`gas_residual_OUT = gas_rico_IN * (1 - retenidos_planta)`, o sea son fracciones
molares del gas de ENTRADA que sobreviven al tratamiento. NO suman 1: la suma
es justamente el rendimiento volumetrico de la planta (por eso `_armar_esquema`
la usa para calcular el volumen de salida).

Entonces hay dos lecturas distintas y las dos importan:

- "% mol del residual"  -> se normaliza por la suma. Es la cromatografia del
  gas que efectivamente sale por el ducto. Es la que se compara contra una
  especificacion de venta.
- "Fraccion sobre el gas de entrada" -> el valor crudo. Mezcla dos efectos
  (cambio de cromato del pool y cambio de retencion), pero es la que cierra
  contra el volumen de salida.

El selector deja elegir; el default es la normalizada.
"""

from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st

try:
    import altair as alt
except ImportError:  # altair viene con streamlit, pero no damos por sentado
    alt = None


PREFIJO_COMPOSICION = "x_"
CORTES_LGN = ["etano", "propano", "butanos", "gasolina"]


# ===========================================================================
# Helpers
# ===========================================================================

def _slug(texto) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(texto).lower()).strip("_")


def _fmt(valor, decimales=1, unidad=""):
    if valor is None:
        return "—"
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if pd.isna(f):
        return "—"
    return f"{f:,.{decimales}f}{unidad}"


def _descargar_csv(df: pd.DataFrame, nombre: str, key: str):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button(
        f"⬇️ Descargar {nombre}.csv",
        data=buf.getvalue(),
        file_name=f"{nombre}.csv",
        mime="text/csv",
        key=key,
    )


def columnas_composicion(serie: pd.DataFrame) -> list[str]:
    """Nombres de compuesto presentes en la serie (sin el prefijo `x_`)."""
    return [c[len(PREFIJO_COMPOSICION):] for c in serie.columns
            if c.startswith(PREFIJO_COMPOSICION)]


def _composicion_larga(serie: pd.DataFrame, planta: str, compuestos: list[str],
                       normalizar: bool) -> pd.DataFrame:
    """Formato largo (periodo, compuesto, valor) para el grafico de composicion.

    OJO con la normalizacion: se divide por la suma de TODOS los compuestos de
    la fila, no por la suma de los seleccionados. Si se dividiera por los
    seleccionados, el grafico cambiaria de escala al tildar o destildar un
    compuesto y dejaria de ser comparable entre corridas.
    """
    todos = columnas_composicion(serie)
    cols = [PREFIJO_COMPOSICION + c for c in todos]

    df = serie[serie["planta"] == planta].copy()
    if df.empty:
        return pd.DataFrame(columns=["periodo", "compuesto", "valor"])

    valores = df[cols].astype(float).fillna(0.0)

    if normalizar:
        total = valores.sum(axis=1).replace(0.0, float("nan"))
        valores = valores.div(total, axis=0) * 100.0

    valores["periodo"] = df["periodo"].values

    largo = valores.melt(id_vars="periodo", var_name="compuesto", value_name="valor")
    largo["compuesto"] = largo["compuesto"].str.removeprefix(PREFIJO_COMPOSICION)
    return largo[largo["compuesto"].isin(compuestos)]


def _lineas(dfl, y_titulo, color="serie", fmt=",.3f", altura=340):
    return (
        alt.Chart(dfl)
        .mark_line(point=True)
        .encode(
            x=alt.X("periodo:T", title=None,
                    axis=alt.Axis(format="%m-%Y", labelAngle=-45)),
            y=alt.Y("valor:Q", title=y_titulo),
            color=alt.Color(f"{color}:N", title=None),
            tooltip=[
                alt.Tooltip("periodo:T", format="%m-%Y", title="Período"),
                alt.Tooltip(f"{color}:N", title=""),
                alt.Tooltip("valor:Q", format=fmt, title=y_titulo),
            ],
        )
        .properties(height=altura)
        .interactive()
    )


def _area_apilada(dfl, y_titulo, color="serie", fmt=",.3f", altura=340):
    return (
        alt.Chart(dfl)
        .mark_area(opacity=0.85)
        .encode(
            x=alt.X("periodo:T", title=None,
                    axis=alt.Axis(format="%m-%Y", labelAngle=-45)),
            y=alt.Y("valor:Q", title=y_titulo, stack="zero"),
            color=alt.Color(f"{color}:N", title=None),
            tooltip=[
                alt.Tooltip("periodo:T", format="%m-%Y", title="Período"),
                alt.Tooltip(f"{color}:N", title=""),
                alt.Tooltip("valor:Q", format=fmt, title=y_titulo),
            ],
        )
        .properties(height=altura)
        .interactive()
    )


def _delta(serie_planta: pd.DataFrame, columna: str):
    """Variacion del ultimo periodo contra el anterior, o None si no hay dos."""
    if len(serie_planta) < 2 or columna not in serie_planta:
        return None
    ult, prev = serie_planta[columna].iloc[-1], serie_planta[columna].iloc[-2]
    if pd.isna(ult) or pd.isna(prev):
        return None
    return float(ult) - float(prev)


# ===========================================================================
# Bloques del panel
# ===========================================================================

def _kpis_sistema(serie: pd.DataFrame):
    """KPI del sistema completo en el ultimo periodo, con delta mes a mes.

    `vol_asignado` y `bypass` SI se suman entre plantas (gas efectivamente
    tratado y gas que no entro a ninguna). `vol_disponible` NO: el disponible
    de una planta es el derivado de la anterior, sumarlo cuenta el mismo gas
    dos o tres veces.
    """
    por_periodo = serie.groupby("periodo").agg(
        gas_tratado=("vol_asignado", "sum"),
        bypass=("bypass", "sum"),
        lgn=("lgn_asignado", "sum"),
        capacidad=("capacidad_evacuacion", "sum"),
    ).reset_index()
    por_periodo["ocupacion"] = (
        por_periodo["lgn"] / por_periodo["capacidad"].replace(0, float("nan")) * 100)

    ult = por_periodo.iloc[-1]
    periodo_txt = pd.Timestamp(ult["periodo"]).strftime("%m-%Y")

    st.caption(f"Sistema completo — último período de la serie: **{periodo_txt}** "
               "(el delta es contra el período anterior).")

    c1, c2, c3, c4 = st.columns(4)
    for col, (etiqueta, campo, dec, unidad) in zip(
        [c1, c2, c3, c4],
        [("LGN producido", "lgn", 0, " tn/d"),
         ("Gas tratado", "gas_tratado", 2, " MMm3/d"),
         ("ByPass total", "bypass", 2, " MMm3/d"),
         ("Ocupación evacuación", "ocupacion", 0, "%")],
    ):
        d = _delta(por_periodo, campo)
        col.metric(etiqueta, _fmt(ult[campo], dec, unidad),
                   delta=None if d is None else _fmt(d, dec, unidad))


def _bloque_composicion(serie: pd.DataFrame):
    st.subheader("Composición del gas residual")

    plantas = sorted(serie["planta"].unique())
    idx_default = plantas.index("MEGA") if "MEGA" in plantas else 0

    c1, c2, c3 = st.columns([2, 3, 2])
    planta = c1.selectbox("Planta", plantas, index=idx_default, key="graf_comp_planta")
    base = c2.radio(
        "Base",
        ["% mol del residual", "Fracción sobre el gas de entrada"],
        horizontal=True, key="graf_comp_base",
        help="El residual se calcula como gas_rico_IN·(1−retención), así que en "
             "crudo NO suma 1: la suma es el rendimiento volumétrico de la "
             "planta. Normalizado es la cromatografía real del gas que sale.",
    )
    vista = c3.radio("Vista", ["Líneas", "Área apilada"], horizontal=True,
                     key="graf_comp_vista")

    normalizar = base.startswith("%")
    todos = columnas_composicion(serie)

    if not todos:
        st.info("La serie no trae columnas de composición.")
        return

    # Default: los compuestos con peso real. Listar los 20 y pico deja un
    # grafico ilegible y todos los trazas pisados contra el eje.
    medias = (serie[serie["planta"] == planta][[PREFIJO_COMPOSICION + c for c in todos]]
              .astype(float).mean())
    relevantes = [c for c in todos
                  if float(medias.get(PREFIJO_COMPOSICION + c, 0)) >= 0.001] or todos[:6]

    compuestos = st.multiselect("Compuestos", todos, default=relevantes,
                                key="graf_comp_compuestos")
    if not compuestos:
        st.info("Elegí al menos un compuesto.")
        return

    largo = _composicion_larga(serie, planta, compuestos, normalizar)
    y_titulo = "% mol" if normalizar else "fracción molar"
    fmt = ",.3f" if normalizar else ",.5f"

    if alt is None:
        st.line_chart(largo.pivot(index="periodo", columns="compuesto", values="valor"))
    else:
        largo = largo.rename(columns={"compuesto": "serie"})
        grafico = (_area_apilada if vista == "Área apilada" else _lineas)(
            largo, y_titulo, fmt=fmt)
        st.altair_chart(grafico, use_container_width=True)

    with st.expander("Ver los datos de este gráfico"):
        ancho = (_composicion_larga(serie, planta, compuestos, normalizar)
                 .pivot(index="periodo", columns="compuesto", values="valor")
                 .reset_index())
        st.dataframe(ancho, use_container_width=True)
        _descargar_csv(ancho, f"composicion_{_slug(planta)}",
                       key=f"dl_comp_{_slug(planta)}")


def _bloque_lgn(serie: pd.DataFrame):
    st.subheader("Producción de LGN vs. capacidad de evacuación")
    st.caption("Línea llena: LGN producido. Punteada: capacidad de evacuación "
               "de esa planta. Cuando se tocan, la planta está llena y el "
               "excedente se deriva o bypasea.")

    largo = serie.melt(
        id_vars=["periodo", "planta"],
        value_vars=["lgn_asignado", "capacidad_evacuacion"],
        var_name="metrica", value_name="valor",
    )
    largo["metrica"] = largo["metrica"].map(
        {"lgn_asignado": "LGN producido", "capacidad_evacuacion": "Capacidad"})

    if alt is None:
        st.line_chart(serie.pivot(index="periodo", columns="planta",
                                  values="lgn_asignado"))
        return

    grafico = (
        alt.Chart(largo)
        .mark_line(point=False)
        .encode(
            x=alt.X("periodo:T", title=None,
                    axis=alt.Axis(format="%m-%Y", labelAngle=-45)),
            y=alt.Y("valor:Q", title="tn/d"),
            color=alt.Color("planta:N", title=None),
            strokeDash=alt.StrokeDash("metrica:N", title=None),
            tooltip=[
                alt.Tooltip("periodo:T", format="%m-%Y", title="Período"),
                alt.Tooltip("planta:N", title="Planta"),
                alt.Tooltip("metrica:N", title=""),
                alt.Tooltip("valor:Q", format=",.1f", title="tn/d"),
            ],
        )
        .properties(height=340)
        .interactive()
    )
    st.altair_chart(grafico, use_container_width=True)

    st.markdown("**Ocupación de la evacuación (%)**")
    ocup = serie[["periodo", "planta", "ocupacion"]].rename(
        columns={"planta": "serie", "ocupacion": "valor"})
    tope = alt.Chart(pd.DataFrame({"y": [100]})).mark_rule(
        strokeDash=[4, 4], color="#C0392B").encode(y="y:Q")
    st.altair_chart(_lineas(ocup, "%", fmt=",.0f", altura=260) + tope,
                    use_container_width=True)


def _bloque_reparto(serie: pd.DataFrame):
    st.subheader("Reparto del gas por planta")
    st.caption("Por eslabón vale `vol_disponible = vol_asignado + vol_derivado "
               "+ bypass`. Las áreas apiladas de una planta suman su disponible.")

    plantas = sorted(serie["planta"].unique())
    planta = st.selectbox("Planta", plantas, key="graf_reparto_planta")

    largo = serie[serie["planta"] == planta].melt(
        id_vars="periodo",
        value_vars=["vol_asignado", "vol_derivado", "bypass"],
        var_name="serie", value_name="valor",
    )
    largo["serie"] = largo["serie"].map({
        "vol_asignado": "Tratado", "vol_derivado": "Derivado", "bypass": "ByPass"})

    if alt is None:
        st.area_chart(largo.pivot(index="periodo", columns="serie", values="valor"))
    else:
        st.altair_chart(_area_apilada(largo, "MMm3/d", fmt=",.2f"),
                        use_container_width=True)


def _bloque_cortes(serie: pd.DataFrame):
    st.subheader("LGN retenido por corte")
    cols = [f"lgn_{c}" for c in CORTES_LGN if f"lgn_{c}" in serie.columns]
    if not cols:
        st.info("La serie no trae el desglose por corte.")
        return

    plantas = sorted(serie["planta"].unique())
    planta = st.selectbox("Planta", plantas, key="graf_cortes_planta")

    largo = serie[serie["planta"] == planta].melt(
        id_vars="periodo", value_vars=cols, var_name="serie", value_name="valor")
    largo["serie"] = largo["serie"].str.removeprefix("lgn_").str.capitalize()

    if alt is None:
        st.area_chart(largo.pivot(index="periodo", columns="serie", values="valor"))
    else:
        st.altair_chart(_area_apilada(largo, "tn/d", fmt=",.1f"),
                        use_container_width=True)

    st.markdown("**Riqueza del gas tratado (tn LGN por MMm3)**")
    st.caption("Es `lgn_unitario` reescalado. Sube cuando el pool se enriquece; "
               "a igual capacidad de evacuación, más riqueza significa MENOS "
               "gas tratable antes de llenarse.")
    riqueza = serie[["periodo", "planta", "lgn_por_mmm3"]].rename(
        columns={"planta": "serie", "lgn_por_mmm3": "valor"})
    st.altair_chart(_lineas(riqueza, "tn/MMm3", fmt=",.1f", altura=260),
                    use_container_width=True)


def _sin_serie(resultados: dict):
    """Fallback: composicion del unico periodo cargado, en barras."""
    st.info(
        "Todavía no hay serie temporal calculada. Cargá el rango en la barra "
        "lateral (**7. Serie temporal**) y apretá **📈 Calcular serie**. "
        "Mientras tanto, abajo está la composición del período actual."
    )

    plantas = resultados.get("plantas", {})
    if not plantas:
        return

    nombres = list(plantas)
    idx = nombres.index("MEGA") if "MEGA" in nombres else 0
    planta = st.selectbox("Planta", nombres, index=idx, key="graf_snap_planta")

    residual = plantas[planta].get("gas_residual_OUT")
    if residual is None:
        st.warning("Esta planta no devolvió `gas_residual_OUT`.")
        return

    s = residual.squeeze() if isinstance(residual, pd.DataFrame) else residual
    if not isinstance(s, pd.Series):
        st.warning("No pude interpretar `gas_residual_OUT` como una composición.")
        return

    df = (s.astype(float).rename("valor").rename_axis("compuesto")
          .reset_index().sort_values("valor", ascending=False))
    df["% mol del residual"] = df["valor"] / df["valor"].sum() * 100

    if alt is None:
        st.bar_chart(df.set_index("compuesto")["% mol del residual"])
    else:
        st.altair_chart(
            alt.Chart(df.head(15)).mark_bar().encode(
                x=alt.X("% mol del residual:Q"),
                y=alt.Y("compuesto:N", sort="-x", title=None),
                tooltip=["compuesto:N",
                         alt.Tooltip("% mol del residual:Q", format=",.3f")],
            ).properties(height=380),
            use_container_width=True,
        )
    _descargar_csv(df, f"composicion_{_slug(planta)}_periodo",
                   key=f"dl_snap_{_slug(planta)}")


# ===========================================================================
# Panel
# ===========================================================================

def panel_graphs(resultados: dict, serie: pd.DataFrame | None = None,
                 fallos: list | None = None):
    st.subheader("Indicadores y evolución")

    if fallos:
        with st.expander(f"⚠️ {len(fallos)} período(s) fallaron al calcular la serie"):
            for periodo, error in fallos:
                st.write(f"- **{pd.Timestamp(periodo).strftime('%m-%Y')}**: {error}")

    if serie is None or len(serie) == 0:
        _sin_serie(resultados)
        return

    serie = serie.sort_values(["planta", "periodo"]).copy()
    serie["periodo"] = pd.to_datetime(serie["periodo"])

    n_periodos = serie["periodo"].nunique()
    if n_periodos == 1:
        st.warning("La serie tiene un solo período: los gráficos de evolución "
                   "van a mostrar un punto. Ampliá el rango en la barra lateral.")

    _kpis_sistema(serie)
    st.divider()

    _bloque_composicion(serie)
    st.divider()

    _bloque_lgn(serie)
    st.divider()

    _bloque_reparto(serie)
    st.divider()

    _bloque_cortes(serie)
    st.divider()

    with st.expander("Ver la serie completa"):
        st.dataframe(serie, use_container_width=True)
        _descargar_csv(serie, "serie_temporal", key="dl_serie")
