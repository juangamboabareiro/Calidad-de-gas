"""
Interfaz Streamlit — Balance de Gas
====================================

Panel pensado para ser mostrado a un equipo comercial: carga el excel de
inputs, corre el pipeline completo (Fase 6 del roadmap) y muestra resultados
en pestañas, con alertas de capacidad y descarga de CSVs.

NOTA IMPORTANTE SOBRE PARÁMETROS EN VIVO
-----------------------------------------
Varios módulos del pipeline (domain/ctes_gas.py, pipeline/preprocesamiento.py,
pipeline/plantas/*.py) leen `config.PATH_INPUTS`, `config.CAPACIDAD`,
`config.CAPACIDAD_MEGA` y `config.FECHA_RANDOM` con `from config import X`
A NIVEL DE MÓDULO. Eso significa que el valor queda "congelado" apenas se
importa el módulo la primera vez: cambiar el archivo subido o el número de
capacidad en la barra lateral de Streamlit, por sí solo, NO tendría ningún
efecto en el resultado del pipeline (aunque la UI parecería funcionar).

Mientras eso no se refactorice en el código fuente (ver recomendación al
final del análisis), este archivo soluciona el problema recargando en
caliente (`importlib.reload`) los módulos afectados, en el orden correcto
de dependencias, cada vez que se aprieta "Ejecutar pipeline". Así el archivo
subido y los parámetros de escenario sí impactan en el resultado.
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
)
from domain.propiedades_gas import calcular_propiedades_gas, calcular_retenidos
from pipeline.inyeccion_std import calcular_inyeccion_std
from pipeline.inyeccion_area import calcular_inyeccion, calcular_inyeccion_area
from pipeline.yacimientos import calcular_inyeccion_yacimientos_areas
from pipeline.detalles_hubs import calcular_inyeccion_detalles_hubs
from pipeline.flujos_directos import calcular_inyeccion_flujos_directos
from pipeline.tabla_total import (
    calcular_tabla_total_yacimientos,
    calcular_tabla_total_flujos_directos,
    calcular_tabla_total_detalles_hubs,
)
from outputs.writers import guardar

st.set_page_config(page_title="Balance de Gas", page_icon="🛢️", layout="wide")


# ---------------------------------------------------------------------------
# Helpers de presentación
# ---------------------------------------------------------------------------

def _a_dataframe_seguro(obj, nombre_valor="Valor"):
    """Convierte Series / DataFrame / escalares a un DataFrame presentable,
    sin asumir la forma exacta que devuelven las funciones de dominio
    (algunas devuelven Series indexadas por compuesto, otras DataFrame)."""
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


def _kpi_capacidad(nombre_planta: str, volumen: float, capacidad: float):
    ocupacion = (volumen / capacidad) if capacidad else 0
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Volumen inyectado — {nombre_planta}", f"{volumen:,.1f}")
    c2.metric("Capacidad configurada", f"{capacidad:,.1f}")
    c3.metric("Ocupación", f"{ocupacion * 100:,.0f}%")
    if volumen > capacidad:
        st.error(
            f"⚠️ **{nombre_planta}** supera la capacidad configurada "
            f"({volumen:,.1f} > {capacidad:,.1f})."
        )
    else:
        st.success(f"✅ **{nombre_planta}** dentro de capacidad.")


def _mostrar_tabla(nombre: str, df: pd.DataFrame, key_prefix: str):
    st.subheader(nombre)
    st.dataframe(df, use_container_width=True)
    _boton_descarga(df, nombre.replace(" ", "_"), key=f"{key_prefix}_{nombre}")


# ---------------------------------------------------------------------------
# Recarga en caliente de módulos sensibles a config.py (ver nota al inicio)
# ---------------------------------------------------------------------------

def _actualizar_config_y_recargar(path, periodo, fecha_random, capacidad, capacidad_mega):
    config.PATH_INPUTS = path
    config.PERIODO_CONSIDERADO = periodo
    config.FECHA_RANDOM = fecha_random
    config.CAPACIDAD = capacidad
    config.CAPACIDAD_MEGA = capacidad_mega

    import domain.ctes_gas as ctes_gas
    importlib.reload(ctes_gas)  # recalcula constantes con el PATH_INPUTS actual

    import pipeline.preprocesamiento as preprocesamiento
    importlib.reload(preprocesamiento)

    import pipeline.plantas.planta_template as planta_template
    importlib.reload(planta_template)  # vuelve a cargar matriz_inyecciones con el path actual

    import pipeline.plantas.MEGA as MEGA
    import pipeline.plantas.TTY_DP as TTY_DP
    import pipeline.plantas.TTY_TBX as TTY_TBX
    importlib.reload(MEGA)
    importlib.reload(TTY_DP)
    importlib.reload(TTY_TBX)

    return {
        "ctes_gas": ctes_gas,
        "preprocesamiento": preprocesamiento,
        "MEGA": MEGA,
        "TTY_DP": TTY_DP,
        "TTY_TBX": TTY_TBX,
    }


# ---------------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------------

st.title("🛢️ Balance de Gas — Panel de resultados")
st.caption("Vista comercial del pipeline de balance de gas y modelado de plantas.")

with st.expander("ℹ️ Cómo leer este panel"):
    st.markdown(
        """
        1. **Subí el excel de inputs** (o dejá el default) y ajustá los parámetros en la barra lateral.
        2. Apretá **Ejecutar pipeline**.
        3. En **Resumen** vas a ver el estado de capacidad de cada planta.
        4. En las demás pestañas están las tablas de detalle, con botón de descarga en CSV.
        """
    )

# ---------------------------------------------------------------------------
# Sidebar: inputs y parámetros
# ---------------------------------------------------------------------------

st.sidebar.header("1. Datos de entrada")
uploaded = st.sidebar.file_uploader(
    "Subir inputs.xlsx (opcional — si no subís nada, se usa el default de config.py)",
    type=["xlsx"],
)

if uploaded is not None:
    tmp_dir = tempfile.mkdtemp()
    input_path = str(Path(tmp_dir) / uploaded.name)
    with open(input_path, "wb") as f:
        f.write(uploaded.getbuffer())
else:
    input_path = config.PATH_INPUTS

st.sidebar.caption(f"Archivo en uso: `{Path(input_path).name}`")

st.sidebar.header("2. Período de análisis")
periodo_str = st.sidebar.text_input(
    "Período considerado (MM-YYYY)",
    value=config.PERIODO_CONSIDERADO.strftime("%m-%Y"),
)
try:
    periodo_ts = pd.Timestamp(periodo_str.replace("/", "-"))
except Exception:
    st.sidebar.error("Formato de período inválido, se usa el default de config.")
    periodo_ts = config.PERIODO_CONSIDERADO

fecha_random_str = st.sidebar.text_input(
    "Fecha de corte corrección butanos (MM-YYYY)",
    value=config.FECHA_RANDOM.strftime("%m-%Y"),
    help="A partir de esta fecha cambia el criterio de corrección de butanos "
         "en TTY-DP / TTY-TBX cuando se supera la capacidad.",
)
try:
    fecha_random_ts = pd.Timestamp(fecha_random_str.replace("/", "-"))
except Exception:
    st.sidebar.error("Formato de fecha inválido, se usa el default de config.")
    fecha_random_ts = config.FECHA_RANDOM

st.sidebar.header("3. Capacidades (escenario)")
capacidad = st.sidebar.number_input(
    "Capacidad TTY (Dew Point / TBX)", value=float(config.CAPACIDAD), step=1.0
)
capacidad_mega = st.sidebar.number_input(
    "Capacidad MEGA", value=float(config.CAPACIDAD_MEGA), step=1.0
)

st.sidebar.header("4. Salidas")
guardar_csvs = st.sidebar.checkbox("Guardar CSVs en disco al ejecutar", value=False)

run = st.sidebar.button("▶️ Ejecutar pipeline", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Ejecución del pipeline
# ---------------------------------------------------------------------------


def ejecutar_pipeline(path, periodo, fecha_random, capacidad, capacidad_mega, guardar_csvs) -> dict:
    mods = _actualizar_config_y_recargar(path, periodo, fecha_random, capacidad, capacidad_mega)
    ctes = mods["ctes_gas"]
    preprocesar_inputs = mods["preprocesamiento"].preprocesar_inputs
    modelar_MEGA = mods["MEGA"].modelar_MEGA
    modelar_TTY_DP = mods["TTY_DP"].modelar_TTY_DP
    modelar_TTY_TBX = mods["TTY_TBX"].modelar_TTY_TBX

    with st.status("Cargando datos de entrada...", expanded=False) as status:
        inyeccion_9300 = load_inyeccion_9300(path)
        coeficientes = load_coeficientes(path)
        retenidos_rtp = load_retenidos_rtp(path)
        flujos_directos = load_flujos_directos(path)
        yacimientos = load_yacimientos(path)
        detalles_hubs = load_detalles_hubs(path)
        propiedades = load_propiedades(path)
        plantas_yacimientos = load_plantas_yacimientos(path)
        status.update(label="Datos cargados ✅", state="complete")

    with st.status("Normalizando y preprocesando...", expanded=False) as status:
        (
            flujos_directos,
            yacimientos,
            detalles_hubs,
            propiedades,
            plantas_yacimientos,
            matriz_inyecciones,
            coefs_inyeccion_area,
            premisas_areas,
        ) = preprocesar_inputs(
            flujos_directos=flujos_directos,
            yacimientos=yacimientos,
            detalles_hubs=detalles_hubs,
            propiedades=propiedades,
            plantas_yacimientos=plantas_yacimientos,
        )
        status.update(label="Preprocesamiento listo ✅", state="complete")

    with st.status("Calculando inyección...", expanded=False) as status:
        inyeccion_std = calcular_inyeccion_std(inyeccion_9300, coeficientes)
        inyeccion = calcular_inyeccion(inyeccion_std, plantas_yacimientos)
        inyeccion_area = calcular_inyeccion_area(inyeccion, matriz_inyecciones)
        status.update(label="Inyección lista ✅", state="complete")

    with st.status("Cruzando yacimientos, hubs y flujos directos...", expanded=False) as status:
        inyeccion_yacimientos_areas = calcular_inyeccion_yacimientos_areas(
            yacimientos, plantas_yacimientos, inyeccion_area
        )
        inyeccion_detalles_hubs = calcular_inyeccion_detalles_hubs(detalles_hubs, plantas_yacimientos)
        inyeccion_flujos_directos = calcular_inyeccion_flujos_directos(flujos_directos, matriz_inyecciones)
        status.update(label="Cruces listos ✅", state="complete")

    with st.status("Construyendo tablas totales...", expanded=False) as status:
        tabla_total_yacimientos = calcular_tabla_total_yacimientos(
            inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area,
            premisas_areas, periodo, ctes.COMPUESTOS,
        )
        tabla_total_flujos_directos = calcular_tabla_total_flujos_directos(
            inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas,
            periodo, ctes.COMPUESTOS,
        )
        tabla_total_detalles_hubs = calcular_tabla_total_detalles_hubs(
            inyeccion_detalles_hubs, premisas_areas
        )

        tabla_total_yacimientos = calcular_propiedades_gas(
            tabla_total_yacimientos, propiedades, ctes.COMPUESTOS,
            ctes.PRESION_BASE, ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS,
            ctes.DENSIDAD_AIRE, ctes.CONVERSION,
        )
        tabla_total_flujos_directos = calcular_propiedades_gas(
            tabla_total_flujos_directos, propiedades, ctes.COMPUESTOS,
            ctes.PRESION_BASE, ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS,
            ctes.DENSIDAD_AIRE, ctes.CONVERSION,
        )
        tabla_total_detalles_hubs = calcular_propiedades_gas(
            tabla_total_detalles_hubs, propiedades, ctes.COMPUESTOS,
            ctes.PRESION_BASE, ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS,
            ctes.DENSIDAD_AIRE, ctes.CONVERSION,
        )
        status.update(label="Tablas totales listas ✅", state="complete")

    if guardar_csvs:
        guardar(tabla_total_yacimientos, "TBL_TTL_YCS", activar=True)
        guardar(tabla_total_flujos_directos, "TBL_TTL_DTOS", activar=True)
        guardar(tabla_total_detalles_hubs, "TBL_TTL_DH", activar=True)

    with st.status("Modelando plantas (Dew Point, TBX, MEGA)...", expanded=False) as status:
        retenidos_TTY_DP = retenidos_rtp[ctes.COMPUESTOS][retenidos_rtp["Planta"] == "Dew point"]
        retenidos_TTY_TBX = retenidos_rtp[ctes.COMPUESTOS][retenidos_rtp["Planta"] == "TBX"]
        retenidos_MEGA = retenidos_rtp[ctes.COMPUESTOS][retenidos_rtp["Planta"] == "TBX MEGA"]

        tabla_tty_dp, gas_rico_dp, gas_residual_dp, ret_dp, ret_vol_dp = modelar_TTY_DP(
            calcular_retenidos=calcular_retenidos,
            tabla_total_flujos_directos=tabla_total_flujos_directos,
            propiedades=propiedades, COMPUESTOS=ctes.COMPUESTOS,
            retenidos_TTY_DP=retenidos_TTY_DP,
        )
        tabla_tty_tbx, gas_rico_tbx, gas_residual_tbx, ret_tbx, ret_vol_tbx = modelar_TTY_TBX(
            calcular_retenidos=calcular_retenidos,
            tabla_total_flujos_directos=tabla_total_flujos_directos,
            propiedades=propiedades, COMPUESTOS=ctes.COMPUESTOS,
            retenidos_TTY_TBX=retenidos_TTY_TBX,
        )
        tabla_mega, gas_rico_mega, gas_residual_mega, ret_mega, ret_vol_mega = modelar_MEGA(
            calcular_retenidos=calcular_retenidos,
            tabla_total_flujos_directos=tabla_total_flujos_directos,
            propiedades=propiedades, COMPUESTOS=ctes.COMPUESTOS,
            retenidos_MEGA=retenidos_MEGA,
        )
        status.update(label="Plantas modeladas ✅", state="complete")

    return {
        "tablas": {
            "Total Yacimientos": tabla_total_yacimientos,
            "Total Flujos Directos": tabla_total_flujos_directos,
            "Total Detalles HUBs": tabla_total_detalles_hubs,
        },
        "plantas": {
            "TTY - Dew Point": {
                "tabla": tabla_tty_dp,
                "gas_rico_in": gas_rico_dp,
                "gas_residual_out": gas_residual_dp,
                "retenidos_vol": ret_vol_dp,
                "capacidad": capacidad,
            },
            "TTY - TBX": {
                "tabla": tabla_tty_tbx,
                "gas_rico_in": gas_rico_tbx,
                "gas_residual_out": gas_residual_tbx,
                "retenidos_vol": ret_vol_tbx,
                "capacidad": capacidad,
            },
            "MEGA": {
                "tabla": tabla_mega,
                "gas_rico_in": gas_rico_mega,
                "gas_residual_out": gas_residual_mega,
                "retenidos_vol": ret_vol_mega,
                "capacidad": capacidad_mega,
            },
        },
    }


if run:
    try:
        st.session_state["resultados"] = ejecutar_pipeline(
            input_path, periodo_ts, fecha_random_ts, capacidad, capacidad_mega, guardar_csvs
        )
        st.sidebar.success("Pipeline ejecutado correctamente.")
    except Exception as e:
        st.sidebar.error(f"Error corriendo el pipeline: {e}")
        st.exception(e)

# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------

resultados = st.session_state.get("resultados")

if resultados is None:
    st.info("Configurá los parámetros en la barra lateral y apretá **▶️ Ejecutar pipeline**.")
else:
    tablas = resultados["tablas"]
    plantas = resultados["plantas"]

    tab_resumen, tab_tablas, tab_dp, tab_tbx, tab_mega = st.tabs(
        ["📊 Resumen", "📋 Tablas totales", "TTY - Dew Point", "TTY - TBX", "MEGA"]
    )

    with tab_resumen:
        st.subheader("Estado de capacidad por planta")
        for nombre_planta, datos in plantas.items():
            volumen = float(datos["tabla"]["Volumen_inyectado"].sum()) if "Volumen_inyectado" in datos["tabla"] else 0.0
            _kpi_capacidad(nombre_planta, volumen, datos["capacidad"])
            st.divider()

    with tab_tablas:
        for nombre, df in tablas.items():
            _mostrar_tabla(nombre, df, key_prefix="tablas")
            st.divider()

    def _mostrar_planta(tab, nombre_planta, datos):
        with tab:
            _kpi_capacidad(
                nombre_planta,
                float(datos["tabla"]["Volumen_inyectado"].sum()) if "Volumen_inyectado" in datos["tabla"] else 0.0,
                datos["capacidad"],
            )
            st.divider()

            with st.expander("Ver tabla de detalle de la planta"):
                _mostrar_tabla(f"Detalle {nombre_planta}", datos["tabla"], key_prefix="planta")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Composición gas rico (entrada)**")
                st.dataframe(_a_dataframe_seguro(datos["gas_rico_in"], "Gas rico IN"), use_container_width=True)
            with c2:
                st.markdown("**Composición gas residual (salida)**")
                st.dataframe(_a_dataframe_seguro(datos["gas_residual_out"], "Gas residual OUT"), use_container_width=True)

            st.markdown("**LGN retenido (etano / propano / butanos / gasolina)**")
            st.dataframe(_a_dataframe_seguro(datos["retenidos_vol"], "Retenido"), use_container_width=True)

    _mostrar_planta(tab_dp, "TTY - Dew Point", plantas["TTY - Dew Point"])
    _mostrar_planta(tab_tbx, "TTY - TBX", plantas["TTY - TBX"])
    _mostrar_planta(tab_mega, "MEGA", plantas["MEGA"])
