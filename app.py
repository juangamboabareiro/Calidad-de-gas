"""
Interfaz Streamlit — Balance de Gas
====================================

Panel para mostrar el pipeline a un equipo comercial: carga el excel de inputs,
corre el modelo y muestra la cascada de plantas, con descarga de CSVs.

MODELO DE CASCADA
-----------------
TTY-DP y TTY-TBX son dos trenes sobre el MISMO pool de gas, no dos plantas en
paralelo. "Llenarse" significa agotar la capacidad de EVACUACION DE LGN (tn/d);
el ingreso de gas rara vez limita y entra solo como min() adicional.

    pre-PM :  pool TTY ─────────────► DP ─(sobra)─► MEGA ─(sobra)─► bypass
              (TBX fuera de servicio)  └─(resto)──► bypass DP

    post-PM:  pool TTY ─► TBX ─(sobra)─► DP ─(sobra)─► MEGA ─(sobra)─► bypass
                           └─(resto)──► bypass TBX

Cada eslabón: se llena hasta `vol_maximo = cap_evacuacion / lgn_unitario`,
deriva el sobrante hasta su tope, y lo que excede el tope es bypass.
El traspaso TBX→DP no es una "derivación" con mezcla: los dos trenes comparten
el pool, así que la cromatografía es idéntica y solo cambia el volumen. La única
derivación real es DP→MEGA, que sí entra a un pool de otra composición.

CROMATOGRAFIA Y POOL DE PLANTA
------------------------------
La cromatografía de cada fila ya no se pega con un merge por `Area`: se busca
por `(Area, Gasoducto)` y, si no hay premisa de ruta, por `Area + Sufijo`. El
sufijo sale de la hoja `Sufijos-Planta` y desambigua las áreas que tienen dos
cromatografías según el destino (Fortín de Piedra: `Planta` vs `Otra`).

El pool de cada planta se arma filtrando por `Gasoducto == nombre_planta` sobre
`flujos_directos` Y `yacimientos`. Antes se mergeaba solo por `Area` contra
flujos directos: eso traía todas las rutas de un origen que se abre a varios
destinos, y descartaba en silencio las áreas que inyectan directo a la planta.

NOTA SOBRE PARAMETROS EN VIVO
-----------------------------
Varios módulos leen `config` a nivel de módulo, así que el valor queda congelado
en el primer import. Mientras eso no se refactorice, se recargan en caliente
(`importlib.reload`) en orden de dependencias en cada ejecución.

UNIDADES
--------
- Volumen_inyectado: unidad de los inputs (10^3 m3 std/d).
- Capacidades de ingreso: en config ya vienen multiplicadas por
  FACTOR_MMm3_A_UNIDAD_VOLUMEN, o sea en unidades de Volumen_inyectado.
  En el sidebar se muestran y editan en MMm3/d.
- retenidos_vol y CAPACIDAD_EVACUACION_*: tn/d.
"""

import importlib
import io
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from io_.loaders import (
    load_inyeccion_9300,
    load_coeficientes,
    load_retenidos_rtp,
    load_flujos_directos,
    load_yacimientos,
    load_detalles_hubs,
    load_propiedades,
    load_plantas_yacimientos,
    load_matriz_inyecciones,
)
from ui.esquemas import mostrar_esquema_planta
from ui.mapa import panel_mapa
from ui.tablas import panel_tablas
from domain.propiedades_gas import calcular_propiedades_gas, calcular_retenidos
from pipeline.inyeccion_std import calcular_inyeccion_std
from pipeline.inyeccion_area import calcular_inyeccion, calcular_inyeccion_area
from pipeline.yacimientos import calcular_inyeccion_yacimientos_areas
from pipeline.detalles_hubs import calcular_detalles_hubs_areas
from pipeline.flujos_directos import calcular_inyeccion_flujos_directos
from pipeline.cromatografia import (
    cargar_sufijos_planta,
    preparar_premisas,
    validar_sufijos,
)
from pipeline.tabla_total import (
    calcular_tabla_total_yacimientos,
    calcular_tabla_total_flujos_directos,
    calcular_tabla_total_detalles_hubs,
)
from outputs.writers import guardar

from ui.diagnosticos import capturar, mostrar as mostrar_diagnostico

from ui.tab_plantas import panel_tab_plantas
from ui.tab_graphs import panel_graphs



st.set_page_config(page_title="Balance de Gas", page_icon="🛢️", layout="wide")


# Unidades: cuántas unidades de Volumen_inyectado hay en 1 MMm3/d.
FACTOR_MM = float(getattr(config, "FACTOR_MMm3_A_UNIDAD_VOLUMEN", 1000.0))

# Poder calorífico de referencia para MMm3eq/d (base 9300 kcal/m3). Si no está
# definido en config no se muestra el equivalente, en vez de inventar un número.
PCS_REFERENCIA = getattr(config, "PCS_REFERENCIA_EQ", None)

COLUMNAS_FLUJOS = [
    "vol_disponible", "vol_maximo", "vol_asignado", "sobrante",
    "vol_derivado", "bypass", "lgn_unitario", "lgn_asignado", "activa",
]


# ===========================================================================
# Helpers de presentación
# ===========================================================================

class _StatusMudo:
    """Reemplazo de `st.status` para las corridas en lote de la serie temporal.

    Sin esto, barrer 24 meses deja 96 widgets de status colgados en la pagina.
    Misma interfaz (`with` + `.update()`) asi `ejecutar_pipeline` no se bifurca.
    """

    def __enter__(self):
        return self

    def __exit__(self, *excepcion):
        return False

    def update(self, **kwargs):
        pass


def _status(label, silencioso):
    return _StatusMudo() if silencioso else st.status(label, expanded=False)


def _a_dataframe_seguro(obj, nombre_valor="Valor"):
    """Convierte Series / DataFrame / escalares a algo presentable."""
    if isinstance(obj, pd.DataFrame):
        return obj.reset_index() if obj.index.name else obj
    if isinstance(obj, pd.Series):
        df = obj.to_frame(name=nombre_valor)
        df.index.name = df.index.name or "Compuesto"
        return df.reset_index()
    try:
        return pd.DataFrame(obj)
    except Exception:
        return pd.DataFrame({nombre_valor: [obj]})


def _boton_descarga(df: pd.DataFrame, nombre: str, key: str):
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    st.download_button(
        f"⬇️ Descargar {nombre}.csv",
        data=csv_buffer.getvalue(),
        file_name=f"{nombre}.csv",
        mime="text/csv",
        key=key,
    )


def _mostrar_tabla(nombre: str, df: pd.DataFrame, key_prefix: str):
    st.subheader(nombre)
    st.dataframe(df, use_container_width=True)
    _boton_descarga(df, nombre.replace(" ", "_"), key=f"{key_prefix}_{nombre}")


def _fmt(valor, decimales=1, unidad=""):
    """Formatea un número, o '—' si no hay dato. 'inf' se muestra como ∞."""
    if valor is None:
        return "—"
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if v == float("inf"):
        return "∞"
    return f"{v:,.{decimales}f}{unidad}"


def _a_mm(vol):
    """Volumen_inyectado -> MMm3/d."""
    if vol is None:
        return None
    try:
        return float(vol) / FACTOR_MM
    except (TypeError, ValueError):
        return None


def _a_eq(vol_mm):
    """MMm3/d -> MMm3eq/d con el PCS de referencia sobre base 9300 kcal/m3.

    El código anterior hacía vol/9300, que no es un equivalente energético
    (mezclaba volumen con poder calorífico). Sin PCS_REFERENCIA_EQ en config se
    muestra '—' en lugar de un número incorrecto.
    """
    if vol_mm is None or PCS_REFERENCIA is None:
        return None
    return float(vol_mm) * float(PCS_REFERENCIA) / 9300.0


def _kpi_planta(nombre_planta: str, datos: dict):
    """KPIs del eslabón. La restricción que manda es la evacuación de LGN."""
    flujos = datos["flujos"]
    cap_evac = datos["capacidad_evacuacion"]

    if not flujos.get("activa", True):
        st.info(f"⏸️ **{nombre_planta}** fuera de servicio en este período "
                f"(anterior a la fecha de PM): el gas pasa directo al siguiente eslabón.")

    c1, c2, c3 = st.columns(3)
    c1.metric("LGN producido", _fmt(flujos["lgn_asignado"], 1, " tn/d"))
    c2.metric("Capacidad de evacuación", _fmt(cap_evac, 1, " tn/d"))
    ocup = (flujos["lgn_asignado"] / cap_evac) if cap_evac else 0
    c3.metric("Ocupación evacuación", f"{ocup * 100:,.0f}%")

    c4, c5, c6 = st.columns(3)
    c4.metric("Gas disponible", _fmt(_a_mm(flujos["vol_disponible"]), 2, " MMm3/d"))
    c5.metric("Gas tratado", _fmt(_a_mm(flujos["vol_asignado"]), 2, " MMm3/d"))
    c6.metric("Tope por evacuación", _fmt(_a_mm(flujos["vol_maximo"]), 2, " MMm3/d"))

    if flujos["sobrante"] > 0:
        st.warning(
            f"⚠️ **{nombre_planta}** se llenó: deriva "
            f"{_fmt(_a_mm(flujos['vol_derivado']), 2)} MMm3/d y bypasea "
            f"{_fmt(_a_mm(flujos['bypass']), 2)} MMm3/d."
        )
    elif flujos["vol_disponible"] > 0:
        st.success(f"✅ **{nombre_planta}** trata todo el gas que le llega.")




def _kpi_origenes(datos: dict):
    """De dónde sale el gas del pool, por tabla de origen.

    El pool se arma filtrando por `Gasoducto == nombre_planta` sobre las dos
    tablas totales. La columna `Origen_tabla` traza cada fila. Sirve para ver de
    un vistazo si una planta está recibiendo inyección directa de áreas
    (`yacimientos`) además del gas que le llega por gasoducto
    (`flujos_directos`), que es justo lo que el armado anterior perdía.
    """
    tabla = datos.get("tabla_total")

    if tabla is None or "Origen_tabla" not in tabla.columns:
        st.caption("Sin traza de origen (tabla armada con la versión anterior).")
        return

    # La fila que agrega una derivación de otra planta no pasa por
    # `armar_input_planta`, así que llega sin `Origen_tabla`. Sin este fillna el
    # groupby la descarta y el traspaso DP -> MEGA desaparece del resumen.
    traza = tabla.copy()
    traza["Origen_tabla"] = traza["Origen_tabla"].fillna("derivacion")

    col_volumen = "Volumen_pool" if "Volumen_pool" in traza.columns else "Volumen_inyectado"

    resumen = traza.groupby("Origen_tabla").agg(
        origenes=("Area", "nunique"), volumen=(col_volumen, "sum"))

    columnas = st.columns(max(len(resumen), 1))
    etiquetas = {
        "flujos_directos": "Vía gasoducto",
        "yacimientos": "Inyección directa",
        "derivacion": "Traspaso de otra planta",
    }

    for col, (origen, fila) in zip(columnas, resumen.iterrows()):
        col.metric(
            etiquetas.get(origen, str(origen)),
            _fmt(_a_mm(fila["volumen"]), 2, " MMm3/d"),
            help=f"{int(fila['origenes'])} orígenes distintos",
        )


def _armar_esquema(datos: dict) -> dict:
    """Traduce el resultado de modelar_* a los campos del esquema SVG."""
    flujos = datos["flujos"]
    rv = datos["retenidos_vol"]

    vol_in_mm = _a_mm(flujos["vol_asignado"])

    # gas_residual_OUT son fracciones molares del gas tratado; el volumen de
    # salida es el asignado por la suma de fracciones que quedan.
    fraccion_residual = float(datos["gas_residual_OUT"].values.sum())
    vol_out_mm = None if vol_in_mm is None else vol_in_mm * fraccion_residual

    bypass_mm = _a_mm(flujos["bypass"])

    etano = float(rv["etano"].values.sum())
    propano = float(rv["propano"].values.sum())
    butanos = float(rv["butanos"].values.sum())
    gasolina = float(rv["gasolina"].values.sum())
    liq_total = etano + propano + butanos + gasolina

    return {
        "flujo_in": vol_in_mm,
        "flujo_in_eq": _a_eq(vol_in_mm),
        "flujo_out": vol_out_mm,
        "flujo_out_eq": _a_eq(vol_out_mm),
        "bypass": bypass_mm,
        "bypass_eq": _a_eq(bypass_mm),
        "derivacion_in": _a_mm(datos.get("recibe_de_vol")),
        "derivacion_out": _a_mm(flujos["vol_derivado"]),
        "rtp": liq_total,
        "liq_total": liq_total,
        "etano": etano,
        "propano": propano,
        "butanos": butanos,
        "gasolina": gasolina,
        "ratio_in_out": (vol_in_mm / vol_out_mm) if vol_out_mm else None,
        
        
    }


def _dot_cascada(plantas: dict, tbx_en_servicio: bool) -> str:
    """Grafo de la cascada, con los volúmenes de cada tramo en MMm3/d."""
    lineas = [
        "digraph G {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fontname="Arial", fontsize=10];',
        '  edge [fontname="Arial", fontsize=9];',
        '  pool [label="Pool TTY", fillcolor="#FDEBD0"];',
        '  poolmega [label="Pool MEGA", fillcolor="#FDEBD0"];',
        '  byp [label="ByPass", shape=ellipse, fillcolor="#FADBD8"];',
    ]
    for nombre, datos in plantas.items():
        color = datos.get("color", "#EAF2F8")
        estilo = "" if datos["flujos"].get("activa", True) else ", style=\"rounded,filled,dashed\""
        lineas.append(f'  "{nombre}" [fillcolor="{color}"{estilo}];')

    primero = "TTY - TBX" if tbx_en_servicio else "TTY - Dew Point"
    lineas.append(f'  pool -> "{primero}" [label="{_fmt(_a_mm(plantas[primero]["flujos"]["vol_disponible"]), 2)}"];')

    if tbx_en_servicio:
        f = plantas["TTY - TBX"]["flujos"]
        lineas.append(f'  "TTY - TBX" -> "TTY - Dew Point" [label="{_fmt(_a_mm(f["vol_derivado"]), 2)}"];')
        if f["bypass"] > 0:
            lineas.append(f'  "TTY - TBX" -> byp [label="{_fmt(_a_mm(f["bypass"]), 2)}", style=dashed];')

    f_dp = plantas["TTY - Dew Point"]["flujos"]
    lineas.append(f'  "TTY - Dew Point" -> "MEGA" [label="{_fmt(_a_mm(f_dp["vol_derivado"]), 2)}"];')
    if f_dp["bypass"] > 0:
        lineas.append(f'  "TTY - Dew Point" -> byp [label="{_fmt(_a_mm(f_dp["bypass"]), 2)}", style=dashed];')

    lineas.append('  poolmega -> "MEGA";')
    f_mega = plantas["MEGA"]["flujos"]
    if f_mega["bypass"] > 0:
        lineas.append(f'  "MEGA" -> byp [label="{_fmt(_a_mm(f_mega["bypass"]), 2)}", style=dashed];')

    lineas.append("}")
    return "\n".join(lineas)


# ===========================================================================
# Recarga en caliente de módulos sensibles a config.py
# ===========================================================================

def _actualizar_config_y_recargar(path, params):
    config.PATH_INPUTS = path
    for nombre, valor in params.items():
        setattr(config, nombre, valor)

    import domain.ctes_gas as ctes_gas
    importlib.reload(ctes_gas)

    import pipeline.preprocesamiento as preprocesamiento
    importlib.reload(preprocesamiento)

    import pipeline.plantas.planta_template as planta_template
    importlib.reload(planta_template)

    import pipeline.plantas.flujo_plantas as flujo_plantas
    importlib.reload(flujo_plantas)

    import pipeline.plantas.TTY as TTY
    import pipeline.plantas.MEGA as MEGA
    importlib.reload(TTY)
    importlib.reload(MEGA)

    return {
        "ctes_gas": ctes_gas,
        "preprocesamiento": preprocesamiento,
        "flujo_plantas": flujo_plantas,
        "TTY": TTY,
        "MEGA": MEGA,
    }


# ===========================================================================
# Encabezado
# ===========================================================================

st.title("🛢️ Balance de Gas — Panel de resultados")
st.caption("Cascada TTY-TBX → TTY-DP → MEGA, limitada por evacuación de LGN.")

with st.expander("ℹ️ Cómo leer este panel"):
    st.markdown(
        """
        1. **Subí el excel de inputs** (o dejá el default) y ajustá los parámetros en la barra lateral.
        2. Apretá **Ejecutar pipeline**.
        3. En **Resumen** está la cascada: cuánto gas trata cada planta, cuánto le pasa
           a la siguiente y cuánto bypasea.

        La restricción activa es la **capacidad de evacuación de LGN** (tn/d), no el
        ingreso de gas. Cada planta se llena hasta ese límite, le pasa el sobrante a
        la siguiente para que igual se trate, y bypasea solo lo que ni así entra.

        **Antes de la fecha de PM**, TTY-TBX está fuera de servicio y todo el pool va
        directo a TTY-DP.
        """
    )

# ===========================================================================
# Sidebar
# ===========================================================================

st.sidebar.header("1. Datos de entrada")
uploaded = st.sidebar.file_uploader(
    "inputs.xlsx (opcional: sin archivo se usa el default de config.py)",
    type=["xlsx", "xlsm"],
)
if uploaded is not None:
    tmp_dir = tempfile.mkdtemp()
    input_path = str(Path(tmp_dir) / uploaded.name)
    with open(input_path, "wb") as f:
        f.write(uploaded.getbuffer())
else:
    input_path = config.PATH_INPUTS
st.sidebar.caption(f"Archivo en uso: `{Path(input_path).name}`")

st.sidebar.header("2. Fechas")
periodo_str = st.sidebar.text_input(
    "Período considerado (MM-YYYY)", value=config.PERIODO_CONSIDERADO.strftime("%m-%Y")
)
try:
    periodo_ts = pd.Timestamp(periodo_str.replace("/", "-"))
except Exception:
    st.sidebar.error("Formato inválido, se usa el default de config.")
    periodo_ts = config.PERIODO_CONSIDERADO

fecha_pm_str = st.sidebar.text_input(
    "Fecha PM TTY-TBX (MM-YYYY)",
    value=config.FECHA_PM_TTY_TBX.strftime("%m-%Y"),
    help="Antes de esta fecha TTY-TBX no está en servicio y todo el pool va a TTY-DP.",
)
try:
    fecha_pm_ts = pd.Timestamp(fecha_pm_str.replace("/", "-"))
except Exception:
    st.sidebar.error("Formato inválido, se usa el default de config.")
    fecha_pm_ts = config.FECHA_PM_TTY_TBX

tbx_en_servicio = periodo_ts >= fecha_pm_ts
if tbx_en_servicio:
    st.sidebar.success("TTY-TBX **en servicio** en este período.")
else:
    st.sidebar.info("TTY-TBX **fuera de servicio**: el pool va directo a TTY-DP.")

st.sidebar.header("3. Evacuación de LGN (tn/d)")
st.sidebar.caption("Restricción activa: define cuánto gas puede tratar cada planta.")
evac_tty_tbx = st.sidebar.number_input(
    "TTY-TBX", value=float(config.CAPACIDAD_EVACUACION_TTY_TBX), step=100.0)
evac_tty_dp = st.sidebar.number_input(
    "TTY-DP", value=float(config.CAPACIDAD_EVACUACION_TTY_DP), step=10.0)
evac_mega = st.sidebar.number_input(
    "MEGA", value=float(config.CAPACIDAD_EVACUACION_MEGA), step=100.0)

st.sidebar.header("4. Traspasos máximos (MMm3/d)")
max_deriv_tbx_dp_mm = st.sidebar.number_input(
    "TTY-TBX → TTY-DP",
    value=float(config.MAX_DERIVACION_TTY_TBX_A_TTY_DP) / FACTOR_MM, step=0.5,
    help="Lo que exceda este tope es bypass de TTY-TBX.")
max_deriv_dp_mega_mm = st.sidebar.number_input(
    "TTY-DP → MEGA",
    value=float(config.MAX_DERIVACION_TTY_DP_A_MEGA) / FACTOR_MM, step=0.5,
    help="Lo que exceda este tope es bypass de TTY-DP.")

with st.sidebar.expander("5. Capacidad de ingreso de gas (MMm3/d)"):
    st.caption("Rara vez limita: entra solo como tope adicional junto a la evacuación.")
    cap_tty_tbx_mm = st.number_input(
        "TTY-TBX", value=float(config.CAPACIDAD_TTY_TBX) / FACTOR_MM, step=1.0)
    cap_tty_dp_mm = st.number_input(
        "TTY-DP", value=float(config.CAPACIDAD_TTY_DP) / FACTOR_MM, step=1.0)
    cap_mega_mm = st.number_input(
        "MEGA", value=float(config.CAPACIDAD_MEGA) / FACTOR_MM, step=1.0)

st.sidebar.header("6. Salidas")
guardar_csvs = st.sidebar.checkbox("Guardar CSVs en disco al ejecutar", value=False)

run = st.sidebar.button("▶️ Ejecutar pipeline", type="primary", use_container_width=True)

st.sidebar.header("7. Serie temporal")
st.sidebar.caption(
    "Alimenta el tab **Graphs**. Corre el pipeline una vez por mes del rango "
    "con los mismos parámetros de capacidad de arriba, así que un rango largo "
    "tarda: son N corridas completas."
)
serie_desde_str = st.sidebar.text_input(
    "Desde (MM-YYYY)",
    value=(periodo_ts - pd.DateOffset(months=11)).strftime("%m-%Y"),
    key="serie_desde",
)
serie_hasta_str = st.sidebar.text_input(
    "Hasta (MM-YYYY)", value=periodo_ts.strftime("%m-%Y"), key="serie_hasta")

try:
    serie_desde = pd.Timestamp(serie_desde_str.replace("/", "-")).normalize()
    serie_hasta = pd.Timestamp(serie_hasta_str.replace("/", "-")).normalize()
    periodos_serie = list(pd.date_range(serie_desde, serie_hasta, freq="MS"))
except Exception:
    st.sidebar.error("Rango inválido (formato MM-YYYY).")
    periodos_serie = []

if periodos_serie:
    st.sidebar.caption(f"{len(periodos_serie)} período(s) en el rango.")
else:
    st.sidebar.caption("El rango no contiene ningún inicio de mes.")

run_serie = st.sidebar.button(
    "📈 Calcular serie", use_container_width=True, disabled=not periodos_serie)

PARAMS = {
    "PERIODO_CONSIDERADO": periodo_ts,
    "FECHA_PM_TTY_TBX": fecha_pm_ts,
    "CAPACIDAD_TTY_TBX": cap_tty_tbx_mm * FACTOR_MM,
    "CAPACIDAD_TTY_DP": cap_tty_dp_mm * FACTOR_MM,
    "CAPACIDAD_MEGA": cap_mega_mm * FACTOR_MM,
    "CAPACIDAD_EVACUACION_TTY_TBX": evac_tty_tbx,
    "CAPACIDAD_EVACUACION_TTY_DP": evac_tty_dp,
    "CAPACIDAD_EVACUACION_MEGA": evac_mega,
    "MAX_DERIVACION_TTY_TBX_A_TTY_DP": max_deriv_tbx_dp_mm * FACTOR_MM,
    "MAX_DERIVACION_TTY_DP_A_MEGA": max_deriv_dp_mega_mm * FACTOR_MM,
}


# ===========================================================================
# Pipeline
# ===========================================================================

def ejecutar_pipeline(path, params, guardar_csvs, silencioso=False) -> dict:
    mods = _actualizar_config_y_recargar(path, params)
    ctes = mods["ctes_gas"]
    preprocesar_inputs = mods["preprocesamiento"].preprocesar_inputs
    modelar_TTY = mods["TTY"].modelar_TTY
    modelar_MEGA = mods["MEGA"].modelar_MEGA
    calcular_DERIVACION = mods["flujo_plantas"].calcular_DERIVACION

    periodo = params["PERIODO_CONSIDERADO"]
    tbx_activa = bool(periodo >= params["FECHA_PM_TTY_TBX"])

    with _status("Cargando datos de entrada...", silencioso) as status:
        inyeccion_9300 = load_inyeccion_9300(path)
        coeficientes = load_coeficientes(path)
        retenidos_rtp = load_retenidos_rtp(path)
        flujos_directos = load_flujos_directos(path)
        yacimientos = load_yacimientos(path)
        detalles_hubs = load_detalles_hubs(path)
        propiedades = load_propiedades(path)
        plantas_yacimientos = load_plantas_yacimientos(path)
        status.update(label="Datos cargados ✅", state="complete")

    with _status("Normalizando y preprocesando...", silencioso) as status:
        inputs = preprocesar_inputs(
            flujos_directos=flujos_directos,
            yacimientos=yacimientos,
            detalles_hubs=detalles_hubs,
            propiedades=propiedades,
            plantas_yacimientos=plantas_yacimientos,
            path_inputs=path,
        )

        flujos_directos      = inputs["flujos_directos"]
        yacimientos          = inputs["yacimientos"]
        detalles_hubs        = inputs["detalles_hubs"]
        propiedades          = inputs["propiedades"]
        plantas_yacimientos  = inputs["plantas_yacimientos"]
        matriz_inyecciones   = inputs["matriz_inyecciones"]
        coefs_inyeccion_area = inputs["coefs_inyeccion_area"]
        premisas_areas       = inputs["premisas_areas"]

        # La hoja de premisas se parte en dos tablas de busqueda: por ruta
        # (Area, Gasoducto) para los gasoductos, y por Area+Sufijo para las
        # areas. `sufijos_planta` es lo que permite distinguir un duplicado que
        # deberia estar desambiguado (Fortin de Piedra) de una inconsistencia
        # de la hoja (Aguada de Castro, cargada dos veces con valores distintos).
        sufijos_planta = cargar_sufijos_planta(path)
        premisas_por_ruta, premisas_por_clave = preparar_premisas(
            premisas_areas, ctes.COMPUESTOS, sufijos_planta)

        status.update(label="Preprocesamiento listo ✅", state="complete")


    with _status("Calculando inyección y tablas totales...", silencioso) as status:
        inyeccion_std = calcular_inyeccion_std(inyeccion_9300, coeficientes)
        inyeccion = calcular_inyeccion(inyeccion_std, plantas_yacimientos)
        inyeccion_area = calcular_inyeccion_area(inyeccion, matriz_inyecciones)

        inyeccion_yacimientos_areas = calcular_inyeccion_yacimientos_areas(
            yacimientos=yacimientos,
            plantas_yacimientos=plantas_yacimientos,
            inyeccion_area=inyeccion_area,
        )[1]          # devuelve (yacimientos_areas, inyeccion_yacimientos_areas)

        detalles_hubs_areas = calcular_detalles_hubs_areas(
            detalles_hubs, plantas_yacimientos)

        inyeccion_flujos_directos = calcular_inyeccion_flujos_directos(
            flujos_directos)

        # El corte de la clave concatenada de Sufijos-Planta se hace por el
        # primer guion. Esto verifica que haya dado nombres de area reales
        # (se rompe si algun dia un area tiene guion en el nombre).
        validar_sufijos(
            sufijos_planta, premisas_areas,
            [inyeccion_yacimientos_areas, inyeccion_flujos_directos])

        tabla_total_yacimientos = calcular_tabla_total_yacimientos(
            inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area,
            premisas_por_ruta, premisas_por_clave, sufijos_planta,
            periodo, ctes.COMPUESTOS)
        tabla_total_flujos_directos = calcular_tabla_total_flujos_directos(
            inyeccion_flujos_directos, coefs_inyeccion_area,
            premisas_por_ruta, premisas_por_clave, sufijos_planta,
            periodo, ctes.COMPUESTOS)
        tabla_total_detalles_hubs = calcular_tabla_total_detalles_hubs(
            detalles_hubs_areas,
            premisas_por_ruta, premisas_por_clave, sufijos_planta,
            ctes.COMPUESTOS)

        tabla_total_yacimientos = calcular_propiedades_gas(
            tabla_total_yacimientos, propiedades, ctes.COMPUESTOS, ctes.PRESION_BASE,
            ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS, ctes.DENSIDAD_AIRE, ctes.CONVERSION)
        tabla_total_flujos_directos = calcular_propiedades_gas(
            tabla_total_flujos_directos, propiedades, ctes.COMPUESTOS, ctes.PRESION_BASE,
            ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS, ctes.DENSIDAD_AIRE, ctes.CONVERSION)
        tabla_total_detalles_hubs = calcular_propiedades_gas(
            tabla_total_detalles_hubs, propiedades, ctes.COMPUESTOS, ctes.PRESION_BASE,
            ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS, ctes.DENSIDAD_AIRE, ctes.CONVERSION)
        status.update(label="Tablas totales listas ✅", state="complete")

    if guardar_csvs:
        guardar(tabla_total_yacimientos, "TBL_TTL_YCS.csv")
        guardar(tabla_total_flujos_directos, "TBL_TTL_DTOS.csv")
        guardar(tabla_total_detalles_hubs, "TBL_TTL_DH.csv")

    with _status("Resolviendo la cascada de plantas...", silencioso) as status:
        retenidos_TTY_DP = retenidos_rtp[ctes.COMPUESTOS][retenidos_rtp["Planta"] == "Dew point"]
        retenidos_TTY_TBX = retenidos_rtp[ctes.COMPUESTOS][retenidos_rtp["Planta"] == "TBX"]
        retenidos_MEGA = retenidos_rtp[ctes.COMPUESTOS][retenidos_rtp["Planta"] == "TBX MEGA"]

        # OJO: `matriz_inyecciones` va la version CRUDA y ANCHA (una columna por
        # destino), no la melteada de `inputs`. `io_plantas` la usa como
        # matriz[nombre_planta] para validar el pool contra la lista de origenes
        # declarada. No reemplazar por inputs["matriz_inyecciones"].
        #
        # `tabla_total_yacimientos` hace falta para MEGA y TBX El Porton, cuyos
        # origenes incluyen areas que inyectan directo a la planta. TTY no la
        # necesita (VMN y VMS son gasoductos) pero pasarla no cambia nada.
        comunes = dict(
            matriz_inyecciones=load_matriz_inyecciones(path),
            calcular_retenidos=calcular_retenidos,
            tabla_total_flujos_directos=tabla_total_flujos_directos,
            tabla_total_yacimientos=tabla_total_yacimientos,
            propiedades=propiedades,
            COMPUESTOS=ctes.COMPUESTOS,
        )

        # 1) TTY-TBX: primer eslabón. Fuera de servicio pre-PM (activa=False),
        #    con tope de traspaso infinito para que el pool pase intacto a DP.
        TTY_TBX = modelar_TTY(
            **comunes,
            retenidos_TTY=retenidos_TTY_TBX,
            CAPACIDAD_EVACUACION_TTY=params["CAPACIDAD_EVACUACION_TTY_TBX"],
            CAPACIDAD_TTY=params["CAPACIDAD_TTY_TBX"],
            MAX_DERIVACION_PLANTA_A_PLANTA=(
                params["MAX_DERIVACION_TTY_TBX_A_TTY_DP"] if tbx_activa else float("inf")),
            activa=tbx_activa,
        )

        # 2) TTY-DP: recibe el sobrante de TBX (o el pool completo pre-PM).
        TTY_DP = modelar_TTY(
            **comunes,
            retenidos_TTY=retenidos_TTY_DP,
            CAPACIDAD_EVACUACION_TTY=params["CAPACIDAD_EVACUACION_TTY_DP"],
            CAPACIDAD_TTY=params["CAPACIDAD_TTY_DP"],
            vol_disponible=TTY_TBX["flujos"]["vol_derivado"],
            MAX_DERIVACION_PLANTA_A_PLANTA=params["MAX_DERIVACION_TTY_DP_A_MEGA"],
        )

        # 3) DP -> MEGA: acá sí es derivación con mezcla (otra composición).
        derivacion_DP_a_MEGA = calcular_DERIVACION(
            flujos_origen=TTY_DP["flujos"],
            gas_rico_IN_origen=TTY_DP["gas_rico_IN"],
            nombre_origen="tty_dp",
        )

        MEGA = modelar_MEGA(
            **comunes,
            retenidos_MEGA=retenidos_MEGA,
            CAPACIDAD_EVACUACION_MEGA=params["CAPACIDAD_EVACUACION_MEGA"],
            CAPACIDAD_MEGA=params["CAPACIDAD_MEGA"],
            derivaciones=[derivacion_DP_a_MEGA],
        )
        status.update(label="Cascada resuelta ✅", state="complete")

    flujos_plantas = pd.DataFrame({
        "TTY - TBX": TTY_TBX["flujos"],
        "TTY - Dew Point": TTY_DP["flujos"],
        "MEGA": MEGA["flujos"],
    }).T.reindex(columns=COLUMNAS_FLUJOS)

    desvio_balance = float(
        (flujos_plantas["vol_disponible"]
         - flujos_plantas[["vol_asignado", "vol_derivado", "bypass"]].sum(axis=1))
        .abs().max()
    )

    red_gasoductos = pd.DataFrame(columns=["origen", "destino", "valor"])
    if {"Area", "Gasoducto", "Volumen_inyectado"}.issubset(tabla_total_yacimientos.columns):
        red_gasoductos = tabla_total_yacimientos[["Area", "Gasoducto", "Volumen_inyectado"]].rename(
            columns={"Area": "origen", "Gasoducto": "destino", "Volumen_inyectado": "valor"})

    plantas = {
        "TTY - TBX": {
            **TTY_TBX,
            "capacidad_evacuacion": params["CAPACIDAD_EVACUACION_TTY_TBX"],
            "capacidad_ingreso": params["CAPACIDAD_TTY_TBX"],
            "recibe_de_vol": None,
            "color": "#5DADE2",
        },
        "TTY - Dew Point": {
            **TTY_DP,
            "capacidad_evacuacion": params["CAPACIDAD_EVACUACION_TTY_DP"],
            "capacidad_ingreso": params["CAPACIDAD_TTY_DP"],
            "recibe_de_vol": TTY_TBX["flujos"]["vol_derivado"],
            "color": "#7FB3D5",
        },
        "MEGA": {
            **MEGA,
            "capacidad_evacuacion": params["CAPACIDAD_EVACUACION_MEGA"],
            "capacidad_ingreso": params["CAPACIDAD_MEGA"],
            "recibe_de_vol": TTY_DP["flujos"]["vol_derivado"],
            "color": "#2E86C1",
        },
    }

    return {
        "tablas": {
            "Total Yacimientos": tabla_total_yacimientos,
            "Total Flujos Directos": tabla_total_flujos_directos,
            "Total Detalles HUBs": tabla_total_detalles_hubs,
        },
        "plantas": plantas,
        "flujos_plantas": flujos_plantas,
        "desvio_balance": desvio_balance,
        "tbx_en_servicio": tbx_activa,
        "red_gasoductos": red_gasoductos,

        # Para el tab "Plantas (sandbox)". `comunes` son los mismos seis inputs
        # que ya reciben modelar_TTY y modelar_MEGA; `retenidos_rtp` es para
        # sembrar la retencion de las tres plantas base.
        "comunes": comunes,
        "retenidos_rtp": retenidos_rtp,
    }


# ===========================================================================
# Serie temporal
# ===========================================================================
#
# El pipeline resuelve UN periodo. Para el tab Graphs se corre una vez por mes
# y cada resultado se aplana en TRES tablas largas, que replican las vistas del
# dashboard Excel del cliente (GRAPHS.pdf):
#
#   "plantas": una fila por (periodo, planta) con flujos, LGN por corte y
#              PCS/IW de entrada y salida.
#   "areas"  : una fila por (periodo, origen_tabla, Area, Gasoducto) con el
#              volumen inyectado -> "Inyeccion por area / por HUB" y el detalle
#              por gasoducto.
#   "pool"   : una fila por (periodo, planta, Area) con el pool de cada planta
#              -> "Ingreso a planta por area / gasoducto".

_PM_AIRE = 28.9647  # kg/kmol, aire estandar. Para la densidad relativa del IW.

_TABLAS_AREAS = {
    "Total Yacimientos": "yacimientos",
    "Total Flujos Directos": "flujos_directos",
    "Total Detalles HUBs": "detalles_hubs",
}


def _col_por_regex(df: pd.DataFrame, patron: str):
    """Primera columna cuyo nombre matchea el patron, o None.

    Los nombres exactos de la hoja `propiedades` ("PCS [kcal/m3]", "Poder
    calorifico...", con o sin tilde) no estan garantizados, asi que se busca
    por regex en vez de hardcodear y reventar con un KeyError."""
    import re as _re
    for col in df.columns:
        if _re.search(patron, str(col), _re.IGNORECASE):
            return col
    return None


def _a_dict_compuestos(obj) -> dict:
    """`gas_residual_OUT` -> {compuesto: fraccion}, sin asumir la forma exacta.

    `io_plantas` lo devuelve como Series-por-DataFrame: gas_rico_IN (Series
    indexada por compuesto) por (1 - retenidos_planta) (DataFrame de una fila),
    o sea un DataFrame 1xN con los compuestos en columnas. Pero TTY_DP puede
    re-modelar con retenciones corregidas, asi que se contempla tambien Series
    y DataFrames de mas de una fila (se suman).
    """
    if obj is None:
        return {}

    if isinstance(obj, pd.DataFrame):
        if obj.shape[0] > 1 and obj.shape[1] > 1:
            serie_comp = obj.sum(axis=0)
        else:
            serie_comp = obj.squeeze()
    else:
        serie_comp = obj

    if not isinstance(serie_comp, pd.Series):
        return {}

    return {str(k): float(v) for k, v in serie_comp.items() if pd.notna(v)}


def _pcs_iw(fracciones, propiedades):
    """(PCS [kcal/m3], Indice de Wobbe) de una mezcla dada en fracciones molares.

    Las fracciones se NORMALIZAN antes de calcular: gas_residual_OUT no suma 1
    (su suma es el rendimiento volumetrico), pero la cromatografia del gas que
    sale por el ducto si es la normalizada.

        PCS_mezcla = sum(x_i * PCS_i)
        IW = PCS / sqrt(densidad relativa),  dens_rel = PM_mezcla / PM_aire

    Devuelve (None, None) si `propiedades` no trae una columna de PCS, y
    (pcs, None) si trae PCS pero no peso molecular. Los graficos del tab
    degradan solos cuando falta el dato.
    """
    if fracciones is None or propiedades is None or not isinstance(propiedades, pd.DataFrame):
        return None, None

    comp = _a_dict_compuestos(fracciones)
    if not comp:
        return None, None
    x = pd.Series(comp, dtype=float)
    total = float(x.sum())
    if total <= 0:
        return None, None
    x = x / total

    col_pcs = _col_por_regex(propiedades, r"pcs|poder\s*calor")
    if col_pcs is None:
        return None, None

    indice = x.index.intersection(propiedades.index)
    if len(indice) == 0:
        return None, None
    x = x.reindex(indice)

    pcs = float((x * pd.to_numeric(propiedades.loc[indice, col_pcs],
                                   errors="coerce").fillna(0)).sum())

    iw = None
    col_pm = _col_por_regex(propiedades, r"peso\s*molecular")
    if col_pm is not None:
        pm = float((x * pd.to_numeric(propiedades.loc[indice, col_pm],
                                      errors="coerce").fillna(0)).sum())
        dens_rel = pm / _PM_AIRE
        if dens_rel > 0:
            iw = pcs / dens_rel ** 0.5

    return pcs, iw


# Hook para el punto de rocio de hidrocarburos de la mezcla (grafico "PRHC"
# de la lamina objetivo). Calcularlo bien requiere un flash con ecuacion de
# estado; inventar una correlacion aca daria numeros que parecen reales y no
# lo son. Si el proyecto expone `domain/prhc.py` con
#
#     def calcular_prhc(fracciones: pd.Series) -> float   # °C
#
# (fracciones molares normalizadas, indexadas por compuesto), la serie lo usa
# y el grafico aparece solo. Mientras no exista, el tab muestra como activarlo.
try:
    from domain.prhc import calcular_prhc
except ImportError:
    calcular_prhc = None


def _grupo_planta(nombre: str) -> str:
    """'TTY - TBX' y 'TTY - Dew Point' -> 'TTY'; el resto queda como esta.
    Es el agrupamiento de la lamina: MEGA / TTY / Directo a gasoducto."""
    if "TTY" in str(nombre).upper():
        return "TTY"
    return str(nombre)


def _fila_mezcla(periodo, resultado, propiedades) -> dict:
    """La corriente que entra al sistema de transporte en un periodo.

    Se compone de:
      - la SALIDA de cada planta: vol_asignado por la fraccion residual
        (suma de gas_residual_OUT), con la composicion residual normalizada;
      - el BYPASS de cada planta, con la composicion del POOL (gas_rico_IN):
        es gas que no se trato, asi que sale con la cromato de entrada.

    El vol_derivado NO se suma: ya esta contado como disponible del eslabon
    siguiente (seria contar el mismo gas dos veces).

    La mezcla molar se pondera por volumen y de ahi salen PCS, IW y (si hay
    hook) el PRHC. El PCS ponderar por volumen es exacto; el IW se calcula
    sobre la composicion mezclada, no promediando IWs (no es lineal).
    """
    ts = pd.Timestamp(periodo).normalize()
    corrientes = []          # (volumen, Series de fracciones normalizadas)
    vol_grupos = {}          # 'MEGA' / 'TTY' / 'Directo a gasoducto'

    for nombre_planta, datos in resultado.get("plantas", {}).items():
        flujos = datos["flujos"]
        grupo = _grupo_planta(nombre_planta)

        residual = _a_dict_compuestos(datos.get("gas_residual_OUT"))
        fraccion_residual = sum(residual.values()) if residual else 0.0
        vol_salida = _a_mm(flujos["vol_asignado"])
        if residual and fraccion_residual > 0 and vol_salida:
            vol_salida = vol_salida * fraccion_residual
            corrientes.append(
                (vol_salida, pd.Series(residual, dtype=float) / fraccion_residual))
            vol_grupos[grupo] = vol_grupos.get(grupo, 0.0) + vol_salida

        rico = _a_dict_compuestos(datos.get("gas_rico_IN"))
        vol_bp = _a_mm(flujos["bypass"])
        if rico and vol_bp and vol_bp > 0:
            corrientes.append((vol_bp, pd.Series(rico, dtype=float)))
            vol_grupos["Directo a gasoducto"] = (
                vol_grupos.get("Directo a gasoducto", 0.0) + vol_bp)

    fila = {"periodo": ts, "pcs": None, "iw": None, "prhc": None}
    for grupo, vol in vol_grupos.items():
        fila[f"vol_{_normalizar_clave(grupo)}"] = vol

    vol_total = sum(v for v, _ in corrientes)
    if vol_total > 0:
        mezcla = pd.Series(dtype=float)
        for vol, x in corrientes:
            mezcla = mezcla.add(x * (vol / vol_total), fill_value=0.0)

        fila["pcs"], fila["iw"] = _pcs_iw(mezcla, propiedades)

        if calcular_prhc is not None:
            try:
                fila["prhc"] = float(calcular_prhc(mezcla))
            except Exception as e:
                # Un mes con composicion rara no debe tirar toda la serie.
                print(f"[serie:{ts:%m-%Y}] calcular_prhc fallo: {e}")

    return fila


def _normalizar_clave(texto: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "_", str(texto).lower()).strip("_")


def _totales_retenidos(retenidos_vol) -> dict:
    if not isinstance(retenidos_vol, pd.DataFrame):
        return {}
    salida = {}
    for corte in ["etano", "propano", "butanos", "gasolina"]:
        if corte in retenidos_vol.columns:
            salida[corte] = float(
                pd.to_numeric(retenidos_vol[corte], errors="coerce").fillna(0).sum())
    return salida


def _fila_serie(periodo, nombre_planta: str, datos: dict, propiedades) -> dict:
    """Aplana el resultado de una planta a una fila. Volumenes ya en MMm3/d."""
    flujos = datos["flujos"]
    cap_evac = datos.get("capacidad_evacuacion")
    lgn = float(flujos["lgn_asignado"])

    pcs_in, iw_in = _pcs_iw(datos.get("gas_rico_IN"), propiedades)
    pcs_out, iw_out = _pcs_iw(datos.get("gas_residual_OUT"), propiedades)

    fila = {
        "periodo": pd.Timestamp(periodo).normalize(),
        "planta": nombre_planta,
        "activa": bool(flujos.get("activa", True)),
        "lgn_asignado": lgn,
        "lgn_unitario": float(flujos["lgn_unitario"]),
        "lgn_por_mmm3": float(flujos["lgn_unitario"]) * FACTOR_MM,
        "capacidad_evacuacion": None if cap_evac is None else float(cap_evac),
        "capacidad_ingreso": _a_mm(datos.get("capacidad_ingreso")),
        "ocupacion": (lgn / float(cap_evac) * 100.0) if cap_evac else None,
        "pcs_in": pcs_in, "pcs_out": pcs_out,
        "iw_in": iw_in, "iw_out": iw_out,
    }

    for col in ["vol_disponible", "vol_maximo", "vol_asignado",
                "sobrante", "vol_derivado", "bypass"]:
        # vol_maximo puede ser inf (planta sin retencion): inf rompe los ejes
        # de altair y contagia NaN al agregar, asi que se guarda como nulo.
        valor = _a_mm(flujos[col])
        fila[col] = None if valor in (float("inf"), float("-inf")) else valor

    for compuesto, valor in _a_dict_compuestos(datos.get("gas_residual_OUT")).items():
        fila[f"x_{compuesto}"] = valor

    for corte, valor in _totales_retenidos(datos.get("retenidos_vol")).items():
        fila[f"lgn_{corte}"] = valor

    return fila


def _filas_areas(periodo, resultado) -> list:
    """Detalle (Area, Gasoducto, volumen) de las tres tablas totales."""
    filas = []
    ts = pd.Timestamp(periodo).normalize()

    for nombre_tabla, origen in _TABLAS_AREAS.items():
        tabla = resultado.get("tablas", {}).get(nombre_tabla)
        if not isinstance(tabla, pd.DataFrame) or tabla.empty:
            continue
        if not {"Area", "Volumen_inyectado"}.issubset(tabla.columns):
            continue

        col_pcs = _col_por_regex(tabla, r"pcs|poder\s*calor")
        tiene_gasoducto = "Gasoducto" in tabla.columns

        for _, r in tabla.iterrows():
            pcs = None
            if col_pcs is not None and pd.notna(r[col_pcs]):
                try:
                    pcs = float(r[col_pcs])
                except (TypeError, ValueError):
                    pcs = None
            filas.append({
                "periodo": ts,
                "origen": origen,
                "area": str(r["Area"]),
                "gasoducto": str(r["Gasoducto"]) if tiene_gasoducto and pd.notna(r["Gasoducto"]) else None,
                "volumen": _a_mm(r["Volumen_inyectado"]),
                "pcs": pcs,
            })
    return filas


def _filas_pool(periodo, resultado) -> list:
    """Pool de cada planta abierto por Area (para 'Ingreso a planta por area')."""
    filas = []
    ts = pd.Timestamp(periodo).normalize()

    for planta, datos in resultado.get("plantas", {}).items():
        tabla = datos.get("tabla_total")
        if not isinstance(tabla, pd.DataFrame) or tabla.empty or "Area" not in tabla.columns:
            continue
        col_pool = "Volumen_pool" if "Volumen_pool" in tabla.columns else "Volumen_inyectado"
        for _, r in tabla.iterrows():
            filas.append({
                "periodo": ts,
                "planta": planta,
                "area": str(r["Area"]),
                "vol_pool": _a_mm(r.get(col_pool)),
                "vol_asignado": _a_mm(r.get("Volumen_inyectado")),
            })
    return filas


def ejecutar_serie(path, params, periodos):
    """Corre el pipeline mes a mes. Devuelve ({"plantas","areas","pool"}, fallos).

    Un mes que revienta no aborta el barrido: se anota en `fallos` y se sigue.
    Es habitual que falten datos de inyeccion para algun periodo del rango y no
    tiene sentido perder los otros 23 por eso.
    """
    filas_plantas, filas_areas, filas_pool, filas_mezcla, fallos = [], [], [], [], []
    barra = st.sidebar.progress(0.0, text="Calculando serie...")

    for i, periodo in enumerate(periodos, start=1):
        etiqueta = pd.Timestamp(periodo).strftime("%m-%Y")
        barra.progress(i / len(periodos), text=f"Serie: {etiqueta} ({i}/{len(periodos)})")

        params_periodo = {**params, "PERIODO_CONSIDERADO": pd.Timestamp(periodo)}
        try:
            # Sin guardar CSVs: escribiria el mismo archivo una vez por mes y
            # solo quedaria el ultimo, que es peor que no escribir nada.
            resultado = ejecutar_pipeline(
                path, params_periodo, guardar_csvs=False, silencioso=True)
        except Exception as e:
            fallos.append((periodo, str(e)))
            continue

        propiedades = resultado.get("comunes", {}).get("propiedades")
        for nombre_planta, datos in resultado["plantas"].items():
            filas_plantas.append(_fila_serie(periodo, nombre_planta, datos, propiedades))

        filas_areas.extend(_filas_areas(periodo, resultado))
        filas_pool.extend(_filas_pool(periodo, resultado))
        filas_mezcla.append(_fila_mezcla(periodo, resultado, propiedades))

    barra.empty()
    serie = {
        "plantas": pd.DataFrame(filas_plantas),
        "areas": pd.DataFrame(filas_areas),
        "pool": pd.DataFrame(filas_pool),
        "mezcla": pd.DataFrame(filas_mezcla),
    }
    return serie, fallos



if run:
    registro = []
    try:
        with capturar() as registro:
            st.session_state["resultados"] = ejecutar_pipeline(
                input_path, PARAMS, guardar_csvs
            )
    except Exception as e:
        st.sidebar.error(f"El pipeline falló: {e}")
        st.exception(e)
    finally:
        st.session_state["diagnostico"] = registro

if run_serie:
    try:
        # Los `print` del pipeline se capturan y descartan: multiplicados por N
        # meses tapan la consola y el diagnostico util es el de la corrida
        # puntual, que ya se muestra en el tab Resumen.
        with capturar():
            serie_dict, fallos_serie = ejecutar_serie(input_path, PARAMS, periodos_serie)
        st.session_state["serie"] = serie_dict
        st.session_state["serie_fallos"] = fallos_serie

        # Si nunca se corrio el pipeline suelto, el resto de los tabs quedarian
        # vacios aunque la serie este lista. Se siembra con el ultimo periodo.
        if st.session_state.get("resultados") is None and len(serie_dict["plantas"]):
            st.session_state["resultados"] = ejecutar_pipeline(
                input_path,
                {**PARAMS, "PERIODO_CONSIDERADO": pd.Timestamp(periodos_serie[-1])},
                guardar_csvs=False,
                silencioso=True,
            )
    except Exception as e:
        st.sidebar.error(f"La serie falló: {e}")
        st.exception(e)



# ===========================================================================
# Resultados
# ===========================================================================

resultados = st.session_state.get("resultados")

if resultados is None:
    st.info("Elegí los parámetros en la barra lateral y apretá **▶️ Ejecutar pipeline**.")
    st.stop()

plantas = resultados["plantas"]
flujos_plantas = resultados["flujos_plantas"]
tbx_en_servicio_res = resultados["tbx_en_servicio"]

(tab_resumen, tab_graphs, tab_cascada, tab_tablas, tab_red,
 tab_tbx, tab_dp, tab_mega, tab_sandbox) = st.tabs(
    ["📊 Resumen", "📈 Graphs", "🔗 Cascada", "📋 Tablas totales", "🗺️ Mapa de la red",
     "TTY - TBX", "TTY - Dew Point", "MEGA", "Plantas (sandbox)"]
)

with tab_resumen:
    # Las observaciones del pipeline van plegadas: en una corrida limpia no
    # aportan nada y empujan hacia abajo lo que sí se mira siempre (el balance
    # y los KPI). El contador en el título deja ver si hay algo sin abrirlo.
    _obs = st.session_state.get("diagnostico", [])
    with st.expander(
        f"🔍 Calidad de los datos de entrada — {len(_obs)} observación(es)"
        if _obs else "🔍 Calidad de los datos de entrada — sin observaciones",
        expanded=False,
    ):
        mostrar_diagnostico(_obs)

    desvio = resultados["desvio_balance"]
    if desvio < 1e-6:
        st.success(f"Balance por eslabón cerrado (desvío máx. {desvio:.2e}).")
    else:
        st.error(
            f"El balance por eslabón no cierra: desvío máx. {desvio:,.4f}. "
            "Debería valer `vol_disponible = vol_asignado + vol_derivado + bypass`."
        )

    st.subheader("Estado de cada eslabón")
    for nombre_planta, datos in plantas.items():
        st.markdown(f"### {nombre_planta}")
        _kpi_planta(nombre_planta, datos)
        st.divider()

    st.subheader("Reparto del gas")
    st.caption(
        "Volúmenes en MMm3/d, LGN en tn/d. Vale "
        "`vol_disponible = vol_asignado + vol_derivado + bypass` por eslabón. "
        "El `vol_derivado` de una planta es el `vol_disponible` de la siguiente, "
        "así que no se pueden sumar las columnas entre plantas."
    )
    vista = flujos_plantas.copy()
    for col in ["vol_disponible", "vol_maximo", "vol_asignado", "sobrante", "vol_derivado", "bypass"]:
        vista[col] = vista[col] / FACTOR_MM
    st.dataframe(
        vista.style.format({
            "vol_disponible": "{:,.2f}", "vol_maximo": "{:,.2f}", "vol_asignado": "{:,.2f}",
            "sobrante": "{:,.2f}", "vol_derivado": "{:,.2f}", "bypass": "{:,.2f}",
            "lgn_unitario": "{:,.5f}", "lgn_asignado": "{:,.1f}",
        }),
        use_container_width=True,
    )
    _boton_descarga(flujos_plantas.reset_index(names="Planta"), "flujos_plantas", key="flujos")

with tab_cascada:
    st.subheader("Cascada del pool de gas")
    if tbx_en_servicio_res:
        st.caption("Post-PM: el pool TTY entra por TTY-TBX. Valores en MMm3/d.")
    else:
        st.caption("Pre-PM: TTY-TBX fuera de servicio, el pool TTY va directo a TTY-DP. "
                   "Valores en MMm3/d.")
    st.graphviz_chart(_dot_cascada(plantas, tbx_en_servicio_res), use_container_width=True)

with tab_graphs:
    panel_graphs(
        resultados,
        serie=st.session_state.get("serie"),
        fallos=st.session_state.get("serie_fallos"),
    )

with tab_tablas:
    panel_tablas(resultados)

with tab_red:
    panel_mapa(resultados)

with tab_sandbox:
    panel_tab_plantas(resultados, PARAMS, FACTOR_MM)


def _mostrar_planta(tab, nombre_planta, datos):
    with tab:
        _kpi_planta(nombre_planta, datos)
        st.divider()

        st.markdown("**Esquema de la planta**")
        mostrar_esquema_planta(
            nombre_planta=nombre_planta,
            color_planta=datos.get("color", "#5DADE2"),
            activa=datos["flujos"].get("activa", True),
            **_armar_esquema(datos),
        )
        st.divider()

        st.markdown("**Origen del pool**")
        st.caption(
            "El pool se arma con todas las filas cuyo `Gasoducto` es esta planta, "
            "tomadas tanto de flujos directos (orígenes que son gasoductos) como de "
            "yacimientos (áreas que inyectan directo)."
        )
        _kpi_origenes(datos)
        st.divider()

        with st.expander("Ver tabla de detalle de la planta"):
            st.caption(
                "`Volumen_pool` es el gas del pool antes del reparto; "
                "`Volumen_inyectado` es la porción efectivamente asignada a esta planta. "
                "`Origen_tabla` dice de qué tabla total salió cada fila. "
                "Si recibe una derivación con otra composición, aparece como fila extra "
                "con el nombre de la planta de origen en `Area`."
            )
            _mostrar_tabla(f"Detalle {nombre_planta}", datos["tabla_total"], key_prefix="planta")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Composición gas rico (entrada)**")
            st.dataframe(_a_dataframe_seguro(datos["gas_rico_IN"], "Gas rico IN"),
                         use_container_width=True)
        with c2:
            st.markdown("**Composición gas residual (salida)**")
            st.dataframe(_a_dataframe_seguro(datos["gas_residual_OUT"].T, "Gas residual OUT"),
                         use_container_width=True)

        st.markdown("**LGN retenido (tn/d) — sobre el gas efectivamente tratado**")
        st.dataframe(_a_dataframe_seguro(datos["retenidos_vol"], "Retenido"),
                     use_container_width=True)


_mostrar_planta(tab_tbx, "TTY - TBX", plantas["TTY - TBX"])
_mostrar_planta(tab_dp, "TTY - Dew Point", plantas["TTY - Dew Point"])
_mostrar_planta(tab_mega, "MEGA", plantas["MEGA"])
