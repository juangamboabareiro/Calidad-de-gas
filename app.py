"""
Interfaz Streamlit para el pipeline de Balance de Gas (post-modularización).

Estructura:
  - Sidebar: carga de inputs.xlsx (o usa el default de config.PATH_INPUTS) +
    parámetros editables (período, capacidad, guardar CSVs).
  - Botón "Ejecutar pipeline": corre la misma secuencia que main.py (Fase 6
    del roadmap) pero con los parámetros que puso el usuario, en memoria
    (session_state), sin depender de reiniciar el proceso.
  - Tabs: uno por DataFrame de salida, con vista + botón de descarga CSV.
  - Sección aparte para los valores de dominio que no son DataFrame
    (calcular_correcciones, alerta de capacidad).

Ajustá los imports y las firmas marcadas con "# TODO" para que coincidan
exactamente con cómo terminaron tus módulos reales.
"""

import io
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from io_ import loaders
from pipeline import (
    preprocesamiento,
    inyeccion_std,
    inyeccion_area,
    yacimientos,
    detalles_hubs,
    flujos_directos,
    tabla_total,
)
from pipeline.plantas.planta_template import io_plantas as modelado_plantas
from domain.propiedades_gas import calcular_retenidos as dom_retenidos

st.set_page_config(page_title="Balance de Gas", layout="wide")
st.title("Balance de Gas — Pipeline")

# ---------------------------------------------------------------------------
# Sidebar: inputs y parámetros
# ---------------------------------------------------------------------------
st.sidebar.header("Inputs")

uploaded = st.sidebar.file_uploader(
    "inputs.xlsx (opcional — si no subís nada, usa el default de config.py)",
    type=["xlsx"],
)

if uploaded is not None:
    tmp_dir = tempfile.mkdtemp()
    input_path = str(Path(tmp_dir) / uploaded.name)
    with open(input_path, "wb") as f:
        f.write(uploaded.getbuffer())
else:
    input_path = config.PATH_INPUTS

st.sidebar.caption(f"Usando: `{input_path}`")

st.sidebar.header("Parámetros")

periodo = st.sidebar.text_input(
    "Período considerado (MM-YYYY)",
    value=config.PERIODO_CONSIDERADO.strftime("%m-%Y"),
)
try:
    periodo_ts = pd.Timestamp(periodo.replace("/", "-"))
except Exception:
    st.sidebar.error("Formato de período inválido, uso el default de config.")
    periodo_ts = config.PERIODO_CONSIDERADO

capacidad = st.sidebar.number_input(
    "Capacidad", value=float(config.CAPACIDAD), step=1.0
)

guardar_csvs = st.sidebar.checkbox("Guardar CSVs en disco al ejecutar", value=False)

run = st.sidebar.button("▶️ Ejecutar pipeline", type="primary")

# ---------------------------------------------------------------------------
# Ejecución del pipeline
# ---------------------------------------------------------------------------


def ejecutar_pipeline(path: str, periodo: pd.Timestamp, capacidad: float) -> dict:
    """Corre la secuencia de main.py y devuelve todo en un dict.

    # TODO: ajustar el orden/argumentos exactos de cada llamada a como
    # terminaron definidas tus funciones reales de pipeline/ y domain/.
    """
    resultados = {}

    with st.status("Cargando inputs...", expanded=False) as status:
        constantes_gas = loaders.load_constantes_gas(path)
        mapa = loaders.load_mapa(path)
        coeficientes = loaders.load_coeficientes(path)
        inyeccion_9300 = loaders.load_inyeccion_9300(path)
        premisas_areas = loaders.load_premisas_areas(path)
        propiedades = loaders.load_propiedades(path)
        matriz_inyecciones = loaders.load_matriz_inyecciones(path)
        flujos_directos_raw = loaders.load_flujos_directos(path)
        yacimientos_raw = loaders.load_yacimientos(path)
        detalles_hubs_raw = loaders.load_detalles_hubs(path)
        coefs_inyeccion_area = loaders.load_coefs_inyeccion_area(path)
        plantas_yacimientos = loaders.load_plantas_yacimientos(path)
        retenidos_rtp = loaders.load_retenidos_rtp(path)
        status.update(label="Inputs cargados ✅")

    with st.status("Preprocesando...", expanded=False) as status:
        flujos_directos_df, yacimientos_df, detalles_hubs_df, propiedades, plantas_yacimientos, matriz_inyecciones, coefs_inyeccion_area, premisas_areas = (
            preprocesamiento.preprocesar_inputs(
                flujos_directos_raw, yacimientos_raw, detalles_hubs_raw,
                propiedades, plantas_yacimientos,
            )
        )
        status.update(label="Preprocesamiento listo ✅")

    with st.status("Corriendo inyección...", expanded=False) as status:
        inyeccion_std = inyeccion_std.calcular_inyeccion_std(inyeccion_9300, coeficientes)
        inyeccion_df, inyeccion_area = inyeccion_area.calcular_inyeccion_area(
            inyeccion_std, plantas_yacimientos, matriz_inyecciones
        )
        status.update(label="Inyección lista ✅")

    with st.status("Yacimientos y hubs...", expanded=False) as status:
        yacimientos_areas, inyeccion_yacimientos_areas = (
            yacimientos.calcular_yacimientos_areas(
                yacimientos_df, plantas_yacimientos, inyeccion_area
            )
        )
        detalles_hubs_areas = detalles_hubs.calcular_detalles_hubs_areas(
            detalles_hubs_df, plantas_yacimientos
        )
        inyeccion_flujos_directos = flujos_directos.calcular_inyeccion_flujos_directos(
            matriz_inyecciones, flujos_directos_df
        )
        status.update(label="Yacimientos/hubs listos ✅")

    with st.status("Tabla total y modelado de plantas...", expanded=False) as status:
        tabla_total_yacimientos = tabla_total.construir_tabla_total(
            inyeccion_yacimientos_areas, coefs_inyeccion_area, premisas_areas,
            propiedades, compuestos=None, periodo=periodo, inyeccion_std=inyeccion_std,
        )
        tabla_total_flujos_directos = tabla_total.construir_tabla_total(
            inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas,
            propiedades, compuestos=None, periodo=periodo,
        )
        tabla_mega, tabla_tty_dp = modelado_plantas.modelar_planta(
            matriz_inyecciones, tabla_total_yacimientos, tipo=None
        )
        status.update(label="Tabla total y modelado listos ✅")

    with st.status("Correcciones (dominio)...", expanded=False) as status:
        correcciones = dom_retenidos.calcular_correcciones(
            volumen_total=None,  # TODO: completar con el DataFrame/valor real
            retenido=retenidos_rtp,
            gas_rico_in=None,
            capacidad=capacidad,
            periodo=periodo,
            fecha_random=periodo,  # ver nota Fase 7 del roadmap: revisar si son el mismo valor
            propiedades=propiedades,
            presion_base=None, constante_gas=None, temperatura_base=None,
            etano=None, propano=None, butanos=None, gasolina=None,
        )
        status.update(label="Correcciones calculadas ✅")

    resultados = {
        "inyeccion_std": inyeccion_std,
        "inyeccion": inyeccion_df,
        "matriz_inyecciones": matriz_inyecciones,
        "inyeccion_area": inyeccion_area,
        "yacimientos_areas": yacimientos_areas,
        "inyeccion_yacimientos_areas": inyeccion_yacimientos_areas,
        "inyeccion_detalles_hubs_areas": detalles_hubs_areas,
        "inyeccion_flujos_directos": inyeccion_flujos_directos,
        "tabla_total_yacimientos": tabla_total_yacimientos,
        "tabla_total_flujos_directos": tabla_total_flujos_directos,
        "tabla_mega": tabla_mega,
        "tabla_tty_dp": tabla_tty_dp,
        "correcciones": correcciones,
    }

    if guardar_csvs:
        for nombre, df in resultados.items():
            if isinstance(df, pd.DataFrame):
                df.to_csv(f"{nombre}.csv", index=False)

    return resultados


if run:
    try:
        st.session_state["resultados"] = ejecutar_pipeline(
            input_path, periodo_ts, capacidad
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
    st.info("Configurá los parámetros en la barra lateral y apretá **Ejecutar pipeline**.")
else:
    tablas = {k: v for k, v in resultados.items() if isinstance(v, pd.DataFrame)}
    correcciones = resultados.get("correcciones")

    tabs = st.tabs(list(tablas.keys()) + ["Correcciones"])

    for tab, (nombre, df) in zip(tabs[:-1], tablas.items()):
        with tab:
            st.subheader(nombre)
            st.dataframe(df, use_container_width=True)
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False)
            st.download_button(
                f"⬇️ Descargar {nombre}.csv",
                data=csv_buffer.getvalue(),
                file_name=f"{nombre}.csv",
                mime="text/csv",
                key=f"download_{nombre}",
            )

    with tabs[-1]:
        st.subheader("Correcciones (valores de dominio, no tabulares)")
        if correcciones is not None:
            st.json(
                correcciones if isinstance(correcciones, dict) else correcciones.to_dict()
            )
            # Alerta de capacidad — TODO: reemplazar por la condición real
            # que hoy imprime el mensaje de alerta en primero.py.
            if isinstance(correcciones, dict) and correcciones.get("capcap", 0) > capacidad:
                st.warning("⚠️ Se superó la capacidad configurada.")
        else:
            st.write("Sin datos.")
