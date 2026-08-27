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
import inspect
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
    load_cromas_hubs,
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
from pipeline.hubs import calcular_ruteo_hubs
from outputs.writers import guardar

from ui.diagnosticos import capturar, mostrar as mostrar_diagnostico

from ui.tab_plantas import panel_tab_plantas
from ui.tab_graphs import panel_graphs



st.set_page_config(page_title="Balance de Gas", page_icon="🛢️", layout="wide")


# Unidades: cuántas unidades de Volumen_inyectado hay en 1 MMm3/d.
FACTOR_MM = float(getattr(config, "FACTOR_MMm3_A_UNIDAD_VOLUMEN", 1000.0))

# `_actualizar_config_y_recargar` sobrescribe config.PATH_INPUTS con el path de
# la corrida. Si eso pasa una vez con un archivo subido, el default de disco
# queda perdido para siempre y la rama "sin archivo" del uploader lee el
# tempdir. Se guarda el valor original en el primer import del proceso.
PATH_INPUTS_DEFAULT = config.PATH_INPUTS

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
        "hubs": "Vía HUB",
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
    input_path = PATH_INPUTS_DEFAULT
st.sidebar.caption(f"Archivo en uso: `{Path(input_path).name}`")

# Las secciones 2 a 7 van dentro de un FORM. Adentro de un form los widgets no
# disparan rerun: se comitean todos juntos cuando apretas un submit. Sin esto,
# el click en "Ejecutar pipeline" se lo comia el blur del campo que estabas
# editando (el blur dispara su propio rerun, y en ese rerun el boton vale
# False), y habia que clickear dos veces por cada campo tocado.
#
# El file_uploader queda AFUERA a proposito: necesita su propio rerun para
# subir el archivo, adentro del form no funcionaria.
#
# Contrapartida: los mensajes derivados (TBX en servicio, cantidad de periodos
# del rango) reflejan los valores del ULTIMO submit, no lo que estas tipeando.
# Se actualizan al apretar cualquiera de los dos botones.
#
# `enter_to_submit=False`: por default un Enter en cualquier text_input o
# number_input del form dispara el submit, o sea corre el pipeline entero sin
# querer. Con esto Enter solo comitea el valor del campo y la unica forma de
# ejecutar es apretar un boton. El parametro existe desde Streamlit 1.39, asi
# que se chequea la firma antes de pasarlo en vez de romper en versiones viejas.
_form_kwargs = (
    {"enter_to_submit": False}
    if "enter_to_submit" in inspect.signature(st.form).parameters
    else {}
)

if not _form_kwargs:
    st.sidebar.caption(
        "⚠️ Streamlit < 1.39: Enter todavía ejecuta el pipeline. "
        "Actualizá (`pip install -U streamlit`) para desactivarlo."
    )

with st.sidebar.form("parametros", **_form_kwargs):

    st.header("2. Fechas")
    periodo_str = st.text_input(
        "Período considerado (MM-YYYY)", value=config.PERIODO_CONSIDERADO.strftime("%m-%Y")
    )
    try:
        periodo_ts = pd.Timestamp(periodo_str.replace("/", "-"))
    except Exception:
        st.error("Formato inválido, se usa el default de config.")
        periodo_ts = config.PERIODO_CONSIDERADO

    # ------------------------------------------------------------------
    # Módulos de planta: la PM (obligatoria) y las ampliaciones (opcionales,
    # tantas filas como se quiera: el editor agrega renglones a demanda).
    # Cada ampliación entra en vigencia cuando su fecha <= período de la
    # corrida, así que en la serie temporal se prenden solas mes a mes.
    # Los Δ son SOBRE las capacidades base de las secciones 5-7.
    # ------------------------------------------------------------------
    _COLS_AMP = {
        "Fecha (MM-YYYY)": st.column_config.TextColumn(
            "Fecha (MM-YYYY)", help="Vigente desde este mes inclusive."),
        "Δ Evacuación [tn/d]": st.column_config.NumberColumn(
            "Δ Evacuación [tn/d]", default=0.0, step=50.0,
            help="Se SUMA a la capacidad de evacuación base."),
        "Δ Ingreso [MMm3/d]": st.column_config.NumberColumn(
            "Δ Ingreso [MMm3/d]", default=0.0, step=0.5,
            help="Se SUMA a la capacidad de ingreso base."),
    }

    def _parsear_ampliaciones(df, etiqueta, con_tren=False):
        """data_editor -> lista de dicts ya en unidades del modelo.

        Filas sin fecha o con ambos Δ en cero se ignoran en silencio (son
        renglones a medio cargar). Fechas mal formateadas avisan y se saltean:
        mejor correr sin esa ampliación que abortar el submit entero.
        """
        ampliaciones = []
        if df is None:
            return ampliaciones
        for _, fila in df.fillna({"Δ Evacuación [tn/d]": 0.0,
                                  "Δ Ingreso [MMm3/d]": 0.0}).iterrows():
            fecha_cruda = str(fila.get("Fecha (MM-YYYY)") or "").strip()
            d_evac = float(fila.get("Δ Evacuación [tn/d]") or 0.0)
            d_ing_mm = float(fila.get("Δ Ingreso [MMm3/d]") or 0.0)
            if not fecha_cruda and d_evac == 0.0 and d_ing_mm == 0.0:
                continue
            try:
                fecha = pd.Timestamp(fecha_cruda.replace("/", "-"))
            except Exception:
                st.warning(f"{etiqueta}: fecha inválida '{fecha_cruda}', "
                           "esa ampliación se ignora.")
                continue
            amp = {"fecha": fecha, "d_evac": d_evac,
                   "d_ingreso": d_ing_mm * FACTOR_MM}
            if con_tren:
                amp["tren"] = str(fila.get("Tren") or "TBX")
                amp["convertible"] = bool(fila.get("Convertible desde DP") or False)
            ampliaciones.append(amp)
        return sorted(ampliaciones, key=lambda a: a["fecha"])

    st.header("3. Módulo TTY")
    fecha_pm_str = st.text_input(
        "Fecha PM TTY-TBX (MM-YYYY)",
        value=config.FECHA_PM_TTY_TBX.strftime("%m-%Y"),
        help="Obligatoria. Antes de esta fecha TTY-TBX no está en servicio y "
             "todo el pool va a TTY-DP.",
    )
    try:
        fecha_pm_ts = pd.Timestamp(fecha_pm_str.replace("/", "-"))
    except Exception:
        st.error("Formato inválido, se usa el default de config.")
        fecha_pm_ts = config.FECHA_PM_TTY_TBX

    tbx_en_servicio = periodo_ts >= fecha_pm_ts
    if tbx_en_servicio:
        st.success("TTY-TBX **en servicio** en este período.")
    else:
        st.info("TTY-TBX **fuera de servicio**: el pool va directo a TTY-DP.")

    st.caption("Ampliaciones (opcionales, agregá tantas filas como quieras):")
    _amp_tty_df = st.data_editor(
        pd.DataFrame(columns=["Fecha (MM-YYYY)", "Tren", "Δ Evacuación [tn/d]",
                              "Δ Ingreso [MMm3/d]", "Convertible desde DP"]),
        num_rows="dynamic",
        key="amp_tty",
        use_container_width=True,
        column_config={
            **_COLS_AMP,
            "Tren": st.column_config.SelectboxColumn(
                "Tren", options=["TBX", "DP"], default="TBX", required=True),
            "Convertible desde DP": st.column_config.CheckboxColumn(
                "Convertible desde DP", default=False,
                help="Solo tiene efecto en ampliaciones del tren TBX: los Δ se "
                     "RESTAN de TTY-DP (capacidad que se convierte de DP a TBX, "
                     "no capacidad nueva)."),
        },
    )
    ampliaciones_tty = _parsear_ampliaciones(_amp_tty_df, "Módulo TTY", con_tren=True)

    st.header("4. Módulo MEGA")
    st.caption("Ampliaciones (opcionales):")
    _amp_mega_df = st.data_editor(
        pd.DataFrame(columns=["Fecha (MM-YYYY)", "Δ Evacuación [tn/d]",
                              "Δ Ingreso [MMm3/d]"]),
        num_rows="dynamic",
        key="amp_mega",
        use_container_width=True,
        column_config=_COLS_AMP,
    )
    ampliaciones_mega = _parsear_ampliaciones(_amp_mega_df, "Módulo MEGA")

    _vigentes = sum(a["fecha"] <= periodo_ts for a in ampliaciones_tty + ampliaciones_mega)
    if ampliaciones_tty or ampliaciones_mega:
        st.caption(f"{len(ampliaciones_tty) + len(ampliaciones_mega)} ampliación(es) "
                   f"cargadas, {_vigentes} vigente(s) al período considerado.")

    st.header("5. Evacuación de LGN (tn/d)")
    st.caption("Restricción activa: define cuánto gas puede tratar cada planta. Son las capacidades BASE; las ampliaciones de los módulos suman encima.")
    evac_tty_tbx = st.number_input(
        "TTY-TBX", value=float(config.CAPACIDAD_EVACUACION_TTY_TBX), step=100.0)
    evac_tty_dp = st.number_input(
        "TTY-DP", value=float(config.CAPACIDAD_EVACUACION_TTY_DP), step=10.0)
    evac_mega = st.number_input(
        "MEGA", value=float(config.CAPACIDAD_EVACUACION_MEGA), step=100.0)

    st.header("6. Traspasos máximos (MMm3/d)")
    max_deriv_tbx_dp_mm = st.number_input(
        "TTY-TBX → TTY-DP",
        value=float(config.MAX_DERIVACION_TTY_TBX_A_TTY_DP) / FACTOR_MM, step=0.5,
        help="Lo que exceda este tope es bypass de TTY-TBX.")
    max_deriv_dp_mega_mm = st.number_input(
        "TTY-DP → MEGA",
        value=float(config.MAX_DERIVACION_TTY_DP_A_MEGA) / FACTOR_MM, step=0.5,
        help="Lo que exceda este tope es bypass de TTY-DP.")

    with st.expander("7. Capacidad de ingreso de gas (MMm3/d)"):
        st.caption("Rara vez limita: entra solo como tope adicional junto a la evacuación.")
        cap_tty_tbx_mm = st.number_input(
            "TTY-TBX", value=float(config.CAPACIDAD_TTY_TBX) / FACTOR_MM, step=1.0)
        cap_tty_dp_mm = st.number_input(
            "TTY-DP", value=float(config.CAPACIDAD_TTY_DP) / FACTOR_MM, step=1.0)
        cap_mega_mm = st.number_input(
            "MEGA", value=float(config.CAPACIDAD_MEGA) / FACTOR_MM, step=1.0)

    st.header("8. Salidas")
    guardar_csvs = st.checkbox("Guardar CSVs en disco al ejecutar", value=False)

    run = st.form_submit_button(
        "▶️ Ejecutar pipeline", type="primary", use_container_width=True)

    st.header("9. Serie temporal")
    st.caption(
        "Alimenta el tab **Graphs**. Corre el pipeline una vez por mes del rango "
        "con las capacidades base de arriba; las ampliaciones de los módulos se "
        "prenden solas en el mes que les corresponde. Un rango largo tarda: son "
        "N corridas completas."
    )
    serie_desde_str = st.text_input(
        "Desde (MM-YYYY)",
        value=(periodo_ts - pd.DateOffset(months=11)).strftime("%m-%Y"),
        key="serie_desde",
    )
    serie_hasta_str = st.text_input(
        "Hasta (MM-YYYY)", value=periodo_ts.strftime("%m-%Y"), key="serie_hasta")

    try:
        serie_desde = pd.Timestamp(serie_desde_str.replace("/", "-")).normalize()
        serie_hasta = pd.Timestamp(serie_hasta_str.replace("/", "-")).normalize()
        periodos_serie = list(pd.date_range(serie_desde, serie_hasta, freq="MS"))
    except Exception:
        st.error("Rango inválido (formato MM-YYYY).")
        periodos_serie = []

    if periodos_serie:
        st.caption(f"{len(periodos_serie)} período(s) en el rango.")
    else:
        st.caption("El rango no contiene ningún inicio de mes.")

    # Sin `disabled`: adentro del form no puede reaccionar a lo que tipeas, se
    # quedaria con el estado del submit anterior. El rango vacio se valida
    # abajo, en el `if run_serie`.
    run_serie = st.form_submit_button(
        "📈 Calcular serie", use_container_width=True)

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
    # Listas de largo libre: cada dict es {fecha, d_evac, d_ingreso} y en TTY
    # ademas {tren, convertible}. Se resuelven POR PERIODO adentro de
    # ejecutar_pipeline, asi la serie temporal las prende mes a mes.
    "AMPLIACIONES_TTY": ampliaciones_tty,
    "AMPLIACIONES_MEGA": ampliaciones_mega,
}


# ===========================================================================
# Pipeline
# ===========================================================================

@st.cache_data(show_spinner=False)
def _mapa_nombres_originales(path) -> dict:
    """{area canonizada -> nombre original de la hoja}, para MOSTRAR.

    El pipeline trabaja con claves canonizadas (minusculas, sin espacios ni
    tildes) para que todo matchee entre hojas. Para presentar en graficos y
    reportes, cada hoja se lee dos veces — canonizada y cruda, mismo orden de
    filas — y se zipean las columnas Area. Es presentacion pura: ninguna
    logica del modelo usa este mapa, asi que no puede romper ningun match.
    """
    mapa = {}
    for loader in (load_yacimientos, load_flujos_directos,
                   load_detalles_hubs, load_plantas_yacimientos):
        try:
            canon = loader(path)["Area"]
            crudo = loader(path, canonizar_area=False)["Area"]
        except Exception as e:  # noqa: BLE001 - una hoja rara no tumba el mapa
            print(f"[nombres] no se pudo mapear una hoja: {e}")
            continue
        for clave, original in zip(canon, crudo):
            if pd.notna(clave) and pd.notna(original):
                mapa.setdefault(str(clave), str(original).strip())
    return mapa


def _cargar_hojas(path, _firma):
    """Las diez lecturas del Excel, cacheadas juntas.

    `_firma` es (mtime, size) del archivo: entra solo para invalidar el cache si
    el excel cambia en disco. No se usa adentro.

    `st.cache_data` devuelve una COPIA en cada acceso, asi que `preprocesar_inputs`
    puede seguir mutando los DataFrames in place sin contaminar el cache.

    OJO: esto NO cachea la lectura que hace `domain.ctes_gas` en su import, que
    `_actualizar_config_y_recargar` rehace con `importlib.reload` en cada corrida.
    Mientras los modulos sigan leyendo config a nivel de modulo, esa queda afuera.
    """
    return {
        "inyeccion_9300": load_inyeccion_9300(path),
        "coeficientes": load_coeficientes(path),
        "retenidos_rtp": load_retenidos_rtp(path),
        "flujos_directos": load_flujos_directos(path),
        "yacimientos": load_yacimientos(path),
        "detalles_hubs": load_detalles_hubs(path),
        "propiedades": load_propiedades(path),
        "plantas_yacimientos": load_plantas_yacimientos(path),
        "matriz_inyecciones": load_matriz_inyecciones(path),
        # Opcional: None si el excel no tiene la hoja. El ruteo por hubs cae
        # entonces a la mezcla volumetrica de las areas de cada hub.
        "cromas_hubs": load_cromas_hubs(path),
        # Presentacion pura: {area canonizada -> nombre original legible}.
        "nombres_areas": _mapa_nombres_originales(path),
    }


def _firma_archivo(path):
    """(mtime, size) para invalidar el cache. Si el path no existe se devuelve
    None y `_cargar_hojas` cachea igual: el error lo tira el loader, como antes."""
    try:
        st_ = Path(path).stat()
        return (st_.st_mtime, st_.st_size)
    except OSError:
        return None


def _aplicar_ampliaciones(params: dict) -> dict:
    """Capacidades EFECTIVAS al período: base + ampliaciones ya vigentes.

    Devuelve una COPIA de params: el dict original (PARAMS) se reusa en las N
    corridas de la serie temporal y cada mes tiene que arrancar de las bases.

    Reglas:
    - Una ampliación rige desde su fecha inclusive (fecha <= período).
    - TTY distingue tren. Si una ampliación de TBX está marcada `convertible`,
      sus Δ se RESTAN de TTY-DP: es capacidad que se convierte de un tren al
      otro, no capacidad nueva del sistema.
    - Nada queda negativo: si las conversiones exceden la capacidad de DP, se
      recorta a cero con aviso (el aviso sale en el panel de diagnósticos).
    """
    p = dict(params)
    periodo = p["PERIODO_CONSIDERADO"]

    aplicadas = []

    for a in p.get("AMPLIACIONES_MEGA", []):
        if a["fecha"] > periodo:
            continue
        p["CAPACIDAD_EVACUACION_MEGA"] += a["d_evac"]
        p["CAPACIDAD_MEGA"] += a["d_ingreso"]
        aplicadas.append(f"MEGA {a['fecha']:%m-%Y}")

    for a in p.get("AMPLIACIONES_TTY", []):
        if a["fecha"] > periodo:
            continue
        if a.get("tren", "TBX") == "TBX":
            p["CAPACIDAD_EVACUACION_TTY_TBX"] += a["d_evac"]
            p["CAPACIDAD_TTY_TBX"] += a["d_ingreso"]
            if a.get("convertible"):
                p["CAPACIDAD_EVACUACION_TTY_DP"] -= a["d_evac"]
                p["CAPACIDAD_TTY_DP"] -= a["d_ingreso"]
                aplicadas.append(f"TTY-TBX {a['fecha']:%m-%Y} (convertida desde DP)")
            else:
                aplicadas.append(f"TTY-TBX {a['fecha']:%m-%Y}")
        else:
            p["CAPACIDAD_EVACUACION_TTY_DP"] += a["d_evac"]
            p["CAPACIDAD_TTY_DP"] += a["d_ingreso"]
            aplicadas.append(f"TTY-DP {a['fecha']:%m-%Y}")

    for clave in ("CAPACIDAD_EVACUACION_TTY_TBX", "CAPACIDAD_EVACUACION_TTY_DP",
                  "CAPACIDAD_EVACUACION_MEGA", "CAPACIDAD_TTY_TBX",
                  "CAPACIDAD_TTY_DP", "CAPACIDAD_MEGA"):
        if p[clave] < 0:
            print(f"[ampliaciones] OJO {clave} quedó negativa "
                  f"({p[clave]:,.1f}) tras las conversiones: se recorta a 0")
            p[clave] = 0.0

    if aplicadas:
        print(f"[ampliaciones] vigentes al {periodo:%m-%Y}: {', '.join(aplicadas)}")

    return p


def _props_croma(croma, propiedades, compuestos, ctes) -> dict | None:
    """z/densidad/PCS/IW de UNA composicion (Serie), renormalizada a suma 1.

    Devuelve None si no hay croma valida. Es la misma cuenta de
    `calcular_propiedades_gas` aplicada a un vector suelto: se usa para el
    gas rico de entrada de cada planta y para la mezcla a transporte.
    """
    if croma is None:
        return None
    if isinstance(croma, pd.DataFrame):
        croma = croma.squeeze()
    if not isinstance(croma, pd.Series):
        return None
    croma = pd.to_numeric(croma.reindex(compuestos), errors="coerce").fillna(0.0)
    total = float(croma.sum())
    if total <= 0:
        return None
    fila = pd.DataFrame([(croma / total).to_dict()])
    fila = calcular_propiedades_gas(
        fila, propiedades, list(compuestos), ctes.PRESION_BASE,
        ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS, ctes.DENSIDAD_AIRE,
        ctes.CONVERSION)
    return {k: float(fila.iloc[0][k]) for k in ("z", "densidad", "PCS", "IW")}


def _mezcla_a_transporte(plantas, propiedades, compuestos, ctes) -> dict:
    """La mezcla que efectivamente entra al sistema de transporte.

    Composicion = residual (renormalizado) de cada planta ponderado por su
    volumen tratado + gas rico bypasseado ponderado por su bypass. El PCS/IW
    salen de la COMPOSICION mezclada, no de promediar PCSs: promediar indices
    de Wobbe es incorrecto porque el IW no es lineal en la mezcla (raiz de la
    densidad en el denominador).

    Volumenes en MMm3/d: vol_tty = tratado por TBX + DP; vol_mega = tratado
    por MEGA; vol_directo_a_gasoducto = suma de bypasses (gas del pool que
    ninguna planta trato). Lo derivado NO suma: ya esta contado como
    disponible del eslabon siguiente.
    """
    acumulada = pd.Series(0.0, index=list(compuestos))
    peso_total = 0.0
    vol_tty = vol_mega = vol_bypass = 0.0

    def _vector(croma):
        if isinstance(croma, pd.DataFrame):
            croma = croma.squeeze()
        if not isinstance(croma, pd.Series):
            return None
        v = pd.to_numeric(croma.reindex(compuestos), errors="coerce").fillna(0.0)
        s = float(v.sum())
        return (v / s) if s > 0 else None

    for nombre, datos in plantas.items():
        flujos = datos.get("flujos", {})
        asignado = float(flujos.get("vol_asignado", 0.0) or 0.0)
        bypass = float(flujos.get("bypass", 0.0) or 0.0)

        residual = _vector(datos.get("gas_residual_OUT"))
        if residual is not None and asignado > 0:
            acumulada = acumulada + residual * asignado
            peso_total += asignado

        rico = _vector(datos.get("gas_rico_IN"))
        if rico is not None and bypass > 0:
            acumulada = acumulada + rico * bypass
            peso_total += bypass

        vol_bypass += bypass
        if "MEGA" in str(nombre).upper():
            vol_mega += asignado
        else:
            vol_tty += asignado

    salida = {
        "vol_mega": vol_mega / FACTOR_MM,
        "vol_tty": vol_tty / FACTOR_MM,
        "vol_directo_a_gasoducto": vol_bypass / FACTOR_MM,
        "pcs": None, "iw": None,
    }
    if peso_total > 0:
        props = _props_croma(acumulada / peso_total, propiedades, compuestos, ctes)
        if props:
            salida["pcs"] = props["PCS"]
            salida["iw"] = props["IW"]
    return salida


def _propiedades_corrientes(plantas, tabla_total_hubs, propiedades, compuestos,
                            ctes):
    """z, densidad, PCS e IW del gas RESIDUAL de cada planta y del gas de
    salida de cada hub. Misma cuenta que para las areas
    (`calcular_propiedades_gas`), aplicada a estas corrientes.

    OJO renormalizacion: `gas_residual_OUT` sale de io_plantas como
    `gas_rico_IN * (1 - retenidos)` y suma MENOS que 1 (le falta lo retenido).
    Para propiedades fisicas la composicion tiene que sumar 1 — los moles
    retenidos ya no estan en la corriente — asi que aca se renormaliza. Las
    fracciones x_ que ya se grafican salen del vector original: no se tocan.

    Los hubs no retienen nada: su "salida" es la croma de tabla_total_hubs tal
    cual (premisa de Cromas-HUBs o mezcla volumetrica), que ya suma ~1.
    """
    filas = []

    for nombre, datos in plantas.items():
        croma = datos.get("gas_residual_OUT")
        if croma is None:
            continue

        # Segun como vengan los retenidos (Serie o DataFrame de una columna),
        # gas_rico_IN * (1 - retenidos) devuelve Serie o DataFrame. Mismo caso
        # que resuelve _fila_derivacion en planta_template: se aplana con
        # squeeze. Si aun asi queda 2D (dos dimensiones reales), no hay una
        # unica composicion que calcular y se saltea con aviso.
        if isinstance(croma, pd.DataFrame):
            croma = croma.squeeze()
        if not isinstance(croma, pd.Series):
            print(f"[propiedades_corrientes] gas_residual_OUT de {nombre} "
                  "no es un vector: se omite")
            continue

        croma = pd.to_numeric(croma.reindex(compuestos), errors="coerce").fillna(0.0)
        total = float(croma.sum())
        if total <= 0:
            continue
        filas.append({"Corriente": nombre, "Tipo": "gas residual planta",
                      **(croma / total).to_dict()})

    if tabla_total_hubs is not None and len(tabla_total_hubs):
        # Un hub aparece una vez por destino con la MISMA croma: una fila por hub.
        for _, f in tabla_total_hubs.drop_duplicates("Area").iterrows():
            filas.append({"Corriente": str(f.get("HUB", f["Area"])),
                          "Tipo": "salida de hub",
                          **{c: float(f.get(c, 0.0)) for c in compuestos}})

    columnas = ["Corriente", "Tipo"] + list(compuestos)
    if not filas:
        return pd.DataFrame(columns=columnas + ["z", "densidad", "PCS", "IW"])

    tabla = pd.DataFrame(filas).reindex(columns=columnas).fillna(0.0)

    return calcular_propiedades_gas(
        tabla, propiedades, list(compuestos), ctes.PRESION_BASE,
        ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS, ctes.DENSIDAD_AIRE,
        ctes.CONVERSION)


def ejecutar_pipeline(path, params, guardar_csvs, silencioso=False) -> dict:
    # Las ampliaciones se resuelven ANTES de recargar config: el sandbox
    # siembra su registro de plantas desde config, y si config quedara con las
    # capacidades base mientras la cascada usa las efectivas, el control del
    # tab "Plantas" daría desvío sin haber bug.
    params = _aplicar_ampliaciones(params)

    mods = _actualizar_config_y_recargar(path, params)
    ctes = mods["ctes_gas"]
    preprocesar_inputs = mods["preprocesamiento"].preprocesar_inputs
    modelar_TTY = mods["TTY"].modelar_TTY
    modelar_MEGA = mods["MEGA"].modelar_MEGA
    calcular_DERIVACION = mods["flujo_plantas"].calcular_DERIVACION

    periodo = params["PERIODO_CONSIDERADO"]
    tbx_activa = bool(periodo >= params["FECHA_PM_TTY_TBX"])

    with _status("Cargando datos de entrada...", silencioso) as status:
        # Cacheado por (path, mtime, size): las 24 corridas de la serie temporal
        # leen el excel una sola vez en total, no una vez por mes.
        hojas = _cargar_hojas(path, _firma_archivo(path))

        inyeccion_9300 = hojas["inyeccion_9300"]
        coeficientes = hojas["coeficientes"]
        retenidos_rtp = hojas["retenidos_rtp"]
        flujos_directos = hojas["flujos_directos"]
        yacimientos = hojas["yacimientos"]
        detalles_hubs = hojas["detalles_hubs"]
        propiedades = hojas["propiedades"]
        plantas_yacimientos = hojas["plantas_yacimientos"]
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

        # --- Ruteo por HUB: area -> HUB -> planta --------------------------
        # Las areas con HUB asignado no inyectan directo a las plantas: su gas
        # entra al hub, que lo mezcla (croma de Cromas-HUBs, o mezcla
        # volumetrica de sus areas si el hub no esta cargado) y lo deriva
        # segun el reparto de los renglones-hub de Detalles-HUBs. Las rutas
        # hacia gasoductos y las areas sin hub (HUB == "Otros") no se tocan.
        #
        # OJO: PISA tabla_total_yacimientos con la version ajustada (sin las
        # rutas ruteadas). Todo lo de aca para abajo — comunes, red del mapa,
        # tablas del panel, CSVs — tiene que ver esa version, por eso va aca
        # y no despues.
        tabla_total_yacimientos, tabla_total_hubs, info_hubs = calcular_ruteo_hubs(
            tabla_total_yacimientos,
            detalles_hubs_areas,          # la ANCHA, con columna HUB
            ctes.COMPUESTOS,
            plantas=params.get("PLANTAS_VIA_HUB",
                               getattr(config, "PLANTAS_VIA_HUB",
                                       ("TTY", "MEGA", "TBX El Porton"))),
            cromas_hubs=hojas["cromas_hubs"],
        )

        # Propiedades fisicas tambien para las filas de hub, asi la tabla se
        # ve completa en el panel (el modelado de plantas no las necesita).
        if len(tabla_total_hubs):
            tabla_total_hubs = calcular_propiedades_gas(
                tabla_total_hubs, propiedades, ctes.COMPUESTOS, ctes.PRESION_BASE,
                ctes.TEMPERATURA_BASE, ctes.CONSTANTE_GAS, ctes.DENSIDAD_AIRE, ctes.CONVERSION)

        status.update(label="Tablas totales listas ✅", state="complete")

    if guardar_csvs:
        guardar(tabla_total_yacimientos, "TBL_TTL_YCS.csv")
        guardar(tabla_total_flujos_directos, "TBL_TTL_DTOS.csv")
        guardar(tabla_total_detalles_hubs, "TBL_TTL_DH.csv")
        if len(tabla_total_hubs):
            guardar(tabla_total_hubs, "TBL_TTL_HUBS.csv")

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
        #
        # `tabla_total_yacimientos` ya viene AJUSTADA por el ruteo de hubs:
        # sin las rutas area->planta de las areas con hub. Esas entran ahora
        # por `tabla_total_hubs`, con la croma del hub. `mapa_area_hub`
        # traduce la validacion contra la matriz de inyecciones, que declara
        # areas como origen pero en el pool aparecen como su hub.
        comunes = dict(
            matriz_inyecciones=hojas["matriz_inyecciones"],
            calcular_retenidos=calcular_retenidos,
            tabla_total_flujos_directos=tabla_total_flujos_directos,
            tabla_total_yacimientos=tabla_total_yacimientos,
            tabla_total_hubs=tabla_total_hubs,
            mapa_area_hub=info_hubs["mapa_area_hub"],
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

    # La red del mapa son los DOS tramos de la cadena, no solo la primaria.
    #
    # Antes salia unicamente de `tabla_total_yacimientos`, o sea aristas
    # area -> gasoducto. Con eso TTY no aparecia nunca en el mapa: sus origenes
    # son VMN y VMS, que son gasoductos y viven en flujos directos, asi que no
    # habia ninguna arista con destino "tty" de donde inferirle una posicion.
    # MEGA si aparecia, porque tiene areas que le inyectan directo.
    #
    # No hay doble conteo: yacimientos aporta area -> gasoducto, flujos
    # directos aporta gasoducto -> destino y hubs aporta hub -> planta. Son
    # tramos distintos de la cadena; en la tabla de hubs Area ES el hub, asi
    # que la arista sale con el nombre del hub como origen.
    _COLS_RED = ["Area", "Gasoducto", "Volumen_inyectado"]

    _tramos = [
        tabla[_COLS_RED]
        for tabla in (tabla_total_yacimientos, tabla_total_flujos_directos,
                      tabla_total_hubs)
        if tabla is not None and set(_COLS_RED).issubset(tabla.columns)
    ]

    if _tramos:
        red_gasoductos = pd.concat(_tramos, ignore_index=True).rename(
            columns={"Area": "origen", "Gasoducto": "destino",
                     "Volumen_inyectado": "valor"})
    else:
        red_gasoductos = pd.DataFrame(columns=["origen", "destino", "valor"])

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

    # Propiedades del gas de salida: residual de plantas + salida de hubs.
    # Va a `tablas` (tab Tablas) y ademas se cuelga de cada planta para que
    # `_fila_serie` las levante y el tab Graphs pueda graficarlas en el tiempo.
    propiedades_corrientes = _propiedades_corrientes(
        plantas, tabla_total_hubs, propiedades, ctes.COMPUESTOS, ctes)

    for _, fila in propiedades_corrientes.iterrows():
        if fila["Tipo"] == "gas residual planta" and fila["Corriente"] in plantas:
            plantas[fila["Corriente"]]["propiedades_residual"] = {
                k: float(fila[k]) for k in ("z", "densidad", "PCS", "IW")}

    # Lo mismo para el gas RICO de entrada (pcs_in / iw_in del tab Graphs) y
    # la mezcla total a transporte (lamina objetivo del dashboard).
    for datos in plantas.values():
        datos["propiedades_rico"] = _props_croma(
            datos.get("gas_rico_IN"), propiedades, ctes.COMPUESTOS, ctes)

    mezcla_transporte = _mezcla_a_transporte(
        plantas, propiedades, ctes.COMPUESTOS, ctes)

    return {
        "tablas": {
            "Total Yacimientos": tabla_total_yacimientos,
            "Total Flujos Directos": tabla_total_flujos_directos,
            "Total Detalles HUBs": tabla_total_detalles_hubs,
            "Total HUBs (ruteo)": tabla_total_hubs,
            "Propiedades gas de salida": propiedades_corrientes,
        },
        "plantas": plantas,
        "flujos_plantas": flujos_plantas,
        "desvio_balance": desvio_balance,
        "tbx_en_servicio": tbx_activa,
        "red_gasoductos": red_gasoductos,

        # Informe del ruteo por hubs: hubs ruteados / sin reparto, mapa
        # area->hub y volumen movido. Lo consume quien quiera (mapa, expander).
        "info_hubs": info_hubs,
        "nombres_areas": hojas.get("nombres_areas") or {},

        # La mezcla que entra al sistema de transporte (volumenes en
        # MMm3/d y PCS/IW de la composicion mezclada). La consume la
        # lamina objetivo del tab Graphs via ejecutar_serie.
        "mezcla_transporte": mezcla_transporte,

        # Para el tab "Plantas (sandbox)". `comunes` son los mismos inputs
        # que ya reciben modelar_TTY y modelar_MEGA (incluida la tabla de hubs
        # y el mapa area->hub); `retenidos_rtp` es para sembrar la retencion
        # de las tres plantas base.
        "comunes": comunes,
        "retenidos_rtp": retenidos_rtp,

        # Parametros YA con las ampliaciones vigentes aplicadas. El tab
        # sandbox siembra su registro con esto y no con PARAMS crudos: si
        # usara las bases mientras la cascada corrio con las efectivas, el
        # control daria desvio sin haber bug.
        "params_efectivos": params,
    }


# ===========================================================================
# Serie temporal
# ===========================================================================
#
# El pipeline resuelve UN periodo. Para el tab de graficos se corre una vez por
# mes y se aplana cada resultado a una fila por (periodo, planta). Todo lo que
# se quiera graficar tiene que salir de aca: el tab no vuelve a tocar el modelo.

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

    return {str(k): float(v) for k, v in serie_comp.items()
            if pd.notna(v)}


def _totales_retenidos(retenidos_vol) -> dict:
    if not isinstance(retenidos_vol, pd.DataFrame):
        return {}
    salida = {}
    for corte in ["etano", "propano", "butanos", "gasolina"]:
        if corte in retenidos_vol.columns:
            salida[corte] = float(
                pd.to_numeric(retenidos_vol[corte], errors="coerce").fillna(0).sum())
    return salida


def _fila_serie(periodo, nombre_planta: str, datos: dict) -> dict:
    """Aplana el resultado de una planta a una fila. Volumenes ya en MMm3/d."""
    flujos = datos["flujos"]
    cap_evac = datos.get("capacidad_evacuacion")
    lgn = float(flujos["lgn_asignado"])

    fila = {
        "periodo": pd.Timestamp(periodo).normalize(),
        "planta": nombre_planta,
        "activa": bool(flujos.get("activa", True)),
        "lgn_asignado": lgn,
        "lgn_unitario": float(flujos["lgn_unitario"]),
        # lgn_unitario es tn por unidad de Volumen_inyectado; reescalado a
        # tn/MMm3 es la "riqueza" del pool, que es la que se lee de un vistazo.
        "lgn_por_mmm3": float(flujos["lgn_unitario"]) * FACTOR_MM,
        "capacidad_evacuacion": None if cap_evac is None else float(cap_evac),
        "ocupacion": (lgn / float(cap_evac) * 100.0) if cap_evac else None,
    }

    for col in ["vol_disponible", "vol_maximo", "vol_asignado",
                "sobrante", "vol_derivado", "bypass"]:
        # vol_maximo puede ser inf (planta sin retencion): inf rompe los ejes
        # de altair y contagia NaN al agregar, asi que se guarda como nulo.
        valor = _a_mm(flujos[col])
        fila[col] = None if valor in (float("inf"), float("-inf")) else valor

    for compuesto, valor in _a_dict_compuestos(datos.get("gas_residual_OUT")).items():
        fila[f"x_{compuesto}"] = valor

    # Propiedades del gas residual (composicion renormalizada), calculadas en
    # ejecutar_pipeline. Columnas residual_z / residual_densidad / residual_PCS
    # / residual_IW, una serie por planta para el tab Graphs.
    for clave, valor in (datos.get("propiedades_residual") or {}).items():
        fila[f"residual_{clave}"] = float(valor)

    # Calidad in/out y capacidad de ingreso, como las espera tab_graphs:
    # entrada = gas rico del pool; salida = residual renormalizado.
    rico = datos.get("propiedades_rico") or {}
    residual = datos.get("propiedades_residual") or {}
    fila["pcs_in"] = rico.get("PCS")
    fila["iw_in"] = rico.get("IW")
    fila["pcs_out"] = residual.get("PCS")
    fila["iw_out"] = residual.get("IW")
    cap_ing = datos.get("capacidad_ingreso")
    fila["capacidad_ingreso"] = _a_mm(cap_ing) if cap_ing not in (None, float("inf")) else None

    for corte, valor in _totales_retenidos(datos.get("retenidos_vol")).items():
        fila[f"lgn_{corte}"] = valor

    return fila


def _nombre_legible(area, hub, nombres: dict) -> str:
    """Nombre para mostrar de un origen: el original de la hoja si existe;
    para los nodos de hub (clave sintetica 'hub...'), la etiqueta del HUB."""
    clave = str(area)
    if clave in nombres:
        return nombres[clave]
    if hub is not None and pd.notna(hub) and str(hub) not in ("", "Otros") \
            and clave.startswith("hub"):
        return f"HUB {hub}"
    return clave


_ORIGEN_TABLAS_SERIE = {
    "Total Yacimientos": "yacimientos",
    "Total Detalles HUBs": "detalles_hubs",
    "Total Flujos Directos": "flujos_directos",
    "Total HUBs (ruteo)": "hubs",
}


def _filas_areas_serie(periodo, resultado, nombres: dict) -> list[dict]:
    """Detalle de inyeccion por (area, gasoducto) para serie["areas"].

    Sale de las tablas totales de la corrida: cada tabla aporta su etiqueta de
    origen y, si la corrida calculo propiedades, el PCS por fila (que el tab
    pondera por volumen para la calidad por gasoducto).
    """
    filas = []
    for nombre_tabla, origen in _ORIGEN_TABLAS_SERIE.items():
        tabla = (resultado.get("tablas") or {}).get(nombre_tabla)
        if tabla is None or not len(tabla) or "Area" not in tabla.columns:
            continue
        for _, f in tabla.iterrows():
            vol = f.get("Volumen_inyectado")
            filas.append({
                "periodo": pd.Timestamp(periodo).normalize(),
                "origen": origen,
                "area": _nombre_legible(f.get("Area"), f.get("HUB"), nombres),
                "gasoducto": f.get("Gasoducto"),
                "volumen": None if pd.isna(vol) else float(vol) / FACTOR_MM,
                "pcs": (float(f["PCS"]) if "PCS" in tabla.columns
                        and pd.notna(f.get("PCS")) else None),
            })
    return filas


def _filas_pool_serie(periodo, resultado, nombres: dict) -> list[dict]:
    """El pool de cada planta abierto por origen, para serie["pool"].

    `vol_pool` es el gas antes del reparto; `vol_asignado`, la porcion que la
    planta trata (la tabla de planta ya viene escalada pro-rata).
    """
    filas = []
    for nombre_planta, datos in (resultado.get("plantas") or {}).items():
        tabla = datos.get("tabla_total")
        if tabla is None or not len(tabla) or "Area" not in tabla.columns:
            continue
        for _, f in tabla.iterrows():
            pool = f.get("Volumen_pool", f.get("Volumen_inyectado"))
            asignado = f.get("Volumen_inyectado")
            filas.append({
                "periodo": pd.Timestamp(periodo).normalize(),
                "planta": nombre_planta,
                "area": _nombre_legible(f.get("Area"), f.get("HUB"), nombres),
                "vol_pool": None if pd.isna(pool) else float(pool) / FACTOR_MM,
                "vol_asignado": (None if pd.isna(asignado)
                                 else float(asignado) / FACTOR_MM),
            })
    return filas


def ejecutar_serie(path, params, periodos):
    """Corre el pipeline mes a mes. Devuelve (serie, fallos).

    `serie` es el dict que consume ui/tab_graphs.panel_graphs:
      - "plantas": una fila por (periodo, planta) con volumenes, LGN por corte,
        pcs_in/pcs_out, iw_in/iw_out y capacidades (ver _fila_serie).
      - "areas": detalle de inyeccion por (area, gasoducto) con origen
        (yacimientos / detalles_hubs / flujos_directos / hubs) y PCS por fila.
      - "pool": el pool de cada planta abierto por origen (vol_pool y
        vol_asignado).
      - "mezcla": una fila por periodo con vol_mega / vol_tty /
        vol_directo_a_gasoducto y el PCS/IW de la mezcla a transporte.
    Todos los volumenes en MMm3/d; PCS/IW en la unidad de Constantes-GAS
    (con Conversion=4.1868, kcal/m3).

    Un mes que revienta no aborta el barrido: se anota en `fallos` y se sigue.
    Es habitual que falten datos de inyeccion para algun periodo del rango y no
    tiene sentido perder los otros 23 por eso.
    """
    filas_plantas, filas_areas, filas_pool, filas_mezcla = [], [], [], []
    fallos = []
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

        for nombre_planta, datos in resultado["plantas"].items():
            filas_plantas.append(_fila_serie(periodo, nombre_planta, datos))

        nombres = resultado.get("nombres_areas") or {}
        filas_areas.extend(_filas_areas_serie(periodo, resultado, nombres))
        filas_pool.extend(_filas_pool_serie(periodo, resultado, nombres))

        # Esquema garantizado: aunque un mes venga sin mezcla_transporte,
        # la fila lleva todas las columnas (en None). Sin esto, un DataFrame
        # sin la columna `pcs` revienta _g_calidad_mezcla con KeyError y ese
        # error mata todos los tabs que se renderizan despues de Graphs.
        mezcla = resultado.get("mezcla_transporte") or {}
        filas_mezcla.append({
            "periodo": pd.Timestamp(periodo).normalize(),
            "vol_mega": None, "vol_tty": None,
            "vol_directo_a_gasoducto": None, "pcs": None, "iw": None,
            **mezcla,
        })

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

if run_serie and not periodos_serie:
    st.sidebar.error("El rango no contiene ningún inicio de mes: no hay nada que correr.")

elif run_serie:
    try:
        # Los `print` del pipeline se capturan y descartan: multiplicados por N
        # meses tapan la consola y el diagnostico util es el de la corrida
        # puntual, que ya se muestra en el tab Resumen.
        with capturar():
            serie_df, fallos_serie = ejecutar_serie(input_path, PARAMS, periodos_serie)
        st.session_state["serie"] = serie_df
        st.session_state["serie_fallos"] = fallos_serie

        # Si nunca se corrio el pipeline suelto, el resto de los tabs quedarian
        # vacios aunque la serie este lista. Se siembra con el ultimo periodo.
        if st.session_state.get("resultados") is None and len(serie_df):
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

_REF_9300 = 9_300.0
UNIDAD_9300 = "MMm³/d de 9.300 kcal"
UNIDAD_STD = "MMm³/d STD"


def construir_vista_9300(resultados: dict):
    """Copia de `resultados` con TODOS los volúmenes en m³ eq. de 9.300 kcal.

    ES SOLO PRESENTACIÓN: el pipeline calcula la física en STD (el balance
    molar y la cascada necesitan metros físicos) y esta vista convierte los
    volúmenes de salida con V_9300 = V_STD × PCS/9300, usando el PCS PROPIO
    de cada corriente:
      - tablas totales: el PCS de cada fila;
      - flujos de planta (disponible/tratado/derivado/bypass/capacidad de
        ingreso): el PCS del gas rico del pool de esa planta;
      - mezcla a transporte: su propio PCS;
      - mapa de la red: el PCS de la fila (Area, Gasoducto) de las tablas.
    Lo que no es volumen de gas (LGN en tn/d, retenidos, cromas, PCS/IW,
    capacidad de evacuación) no se toca. Lo que RE-CALCULA física (comunes
    del sandbox, serie) recibe siempre el original en STD.

    Devuelve (vista, avisos). Si los PCS no están en kcal/m³ (Conversion=1000
    en Constantes-GAS), no convierte y lo dice: dividir MJ por 9300 daría
    basura con cara de número.
    """
    avisos = []

    # Chequeo de escala con el PCS de las tablas.
    _pcs_muestra = []
    for t in (resultados.get("tablas") or {}).values():
        if t is not None and len(t) and "PCS" in t.columns:
            _pcs_muestra.append(t["PCS"].dropna())
    muestra = pd.concat(_pcs_muestra) if _pcs_muestra else pd.Series(dtype=float)
    if not len(muestra):
        return resultados, ["Las tablas no traen PCS: no se puede convertir a "
                            "9.300 (¿corrida vieja en memoria?). Se muestra STD."]
    if muestra.median() < 1000:
        return resultados, ["Los PCS no están en kcal/m³ (¿Conversion=1000 en "
                            "Constantes-GAS?): se muestra STD."]

    vista = dict(resultados)

    # --- Tablas totales: factor por fila ---------------------------------
    tablas_v = {}
    for nombre, t in (resultados.get("tablas") or {}).items():
        if t is None or not len(t) or "Volumen_inyectado" not in t.columns:
            tablas_v[nombre] = t
            continue
        t2 = t.copy()
        if "PCS" in t2.columns and t2["PCS"].notna().any():
            f = t2["PCS"] / _REF_9300
            t2["Volumen_inyectado"] = t2["Volumen_inyectado"] * f
            for c in t2.columns:
                if c.startswith("Vol_"):
                    t2[c] = t2[c] * f
        else:
            avisos.append(f"'{nombre}' sin PCS por fila: queda en STD.")
        tablas_v[nombre] = t2
    vista["tablas"] = tablas_v

    # --- Plantas: factor = PCS del gas rico del pool ----------------------
    plantas_v = {}
    for nombre, datos in (resultados.get("plantas") or {}).items():
        pcs = (datos.get("propiedades_rico") or {}).get("PCS")
        if not pcs or pcs < 1000:
            avisos.append(f"{nombre} sin PCS de gas rico: sus flujos quedan en STD.")
            plantas_v[nombre] = datos
            continue
        f = pcs / _REF_9300
        d2 = dict(datos)

        flujos = dict(datos.get("flujos") or {})
        for k in ("vol_disponible", "vol_maximo", "vol_asignado", "sobrante",
                  "vol_derivado", "bypass"):
            v = flujos.get(k)
            if isinstance(v, (int, float)) and v not in (float("inf"),):
                flujos[k] = v * f
        if isinstance(flujos.get("derivados"), dict):
            flujos["derivados"] = {k: v * f for k, v in flujos["derivados"].items()}
        d2["flujos"] = flujos
        d2["bypass"] = flujos.get("bypass", datos.get("bypass"))

        for k in ("vol_pool", "capacidad_ingreso", "recibe_de_vol"):
            v = datos.get(k)
            if isinstance(v, (int, float)) and v != float("inf"):
                d2[k] = v * f

        tabla = datos.get("tabla_total")
        if tabla is not None and len(tabla):
            t2 = tabla.copy()
            # Fila a fila si la tabla trae PCS; si no, el factor de la planta
            # (aprox.: el pool comparte destino, no composicion).
            ff = (t2["PCS"] / _REF_9300) if "PCS" in t2.columns \
                and t2["PCS"].notna().any() else f
            for c in ("Volumen_pool", "Volumen_inyectado"):
                if c in t2.columns:
                    t2[c] = t2[c] * ff
            d2["tabla_total"] = t2

        plantas_v[nombre] = d2
    vista["plantas"] = plantas_v

    # --- flujos_plantas (KPIs del resumen): factor por planta -------------
    fp = resultados.get("flujos_plantas")
    if fp is not None and len(fp):
        fp2 = fp.copy()
        for nombre in fp2.index:
            pcs = ((resultados["plantas"].get(nombre) or {})
                   .get("propiedades_rico") or {}).get("PCS")
            if pcs and pcs > 1000:
                cols = [c for c in ("vol_disponible", "vol_asignado",
                                    "vol_derivado", "bypass", "vol_maximo",
                                    "sobrante")
                        if c in fp2.columns]
                fp2.loc[nombre, cols] = fp2.loc[nombre, cols] * (pcs / _REF_9300)
        vista["flujos_plantas"] = fp2

    # --- mezcla a transporte ----------------------------------------------
    mez = resultados.get("mezcla_transporte")
    if mez and mez.get("pcs"):
        f = mez["pcs"] / _REF_9300
        vista["mezcla_transporte"] = {
            **mez, **{k: (mez[k] * f if isinstance(mez.get(k), (int, float))
                          else mez.get(k))
                      for k in ("vol_mega", "vol_tty", "vol_directo_a_gasoducto")}}

    # --- mapa de la red: PCS por (Area, Gasoducto) desde las tablas -------
    red = resultados.get("red_gasoductos")
    if red is not None and len(red) and "Volumen_inyectado" in red.columns:
        pares = []
        for t in (resultados.get("tablas") or {}).values():
            if t is not None and len(t) and "PCS" in getattr(t, "columns", ()):
                pares.append(t[["Area", "Gasoducto", "PCS"]])
        if pares:
            lookup = (pd.concat(pares).dropna(subset=["PCS"])
                      .drop_duplicates(["Area", "Gasoducto"]))
            red2 = red.merge(lookup, on=["Area", "Gasoducto"], how="left")
            factor = (red2["PCS"] / _REF_9300).fillna(1.0)
            red2["Volumen_inyectado"] = red2["Volumen_inyectado"] * factor
            vista["red_gasoductos"] = red2.drop(columns=["PCS"])

    return vista, avisos


resultados = st.session_state.get("resultados")

if resultados is None:
    st.info("Elegí los parámetros en la barra lateral y apretá **▶️ Ejecutar pipeline**.")
    st.stop()

# ---------------------------------------------------------------------------
# Unidad de los volúmenes de TODA la app (fuera del form: cambia al instante,
# sin recorrer el pipeline, porque es pura conversión de presentación).
# ---------------------------------------------------------------------------
unidad_volumen = st.sidebar.radio(
    "🧭 Unidad de volúmenes", [UNIDAD_9300, UNIDAD_STD], index=0,
    key="unidad_volumen_global",
    help="Aplica a KPIs, tabs de plantas, tablas, mapa y Graphs. STD: metros "
         "cúbicos físicos. 9.300: equivalentes en energía (V₉₃₀₀ = V_STD × "
         "PCS/9300, con el PCS propio de cada corriente). El sandbox re-modela "
         "la física y trabaja siempre en STD.")

# El original en STD queda para lo que RE-CALCULA: sandbox (comunes) y serie.
resultados_fisicos = resultados
if unidad_volumen == UNIDAD_9300:
    resultados, _avisos_unidad = construir_vista_9300(resultados)
    for _a in _avisos_unidad:
        st.sidebar.warning(_a)
st.sidebar.caption(f"Mostrando volúmenes en **{unidad_volumen}**.")

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

def _render_seguro(nombre_tab: str, fn, *args, **kwargs):
    """Ejecuta el contenido de un tab aislando sus errores.

    Los `with tab:` de Streamlit corren en orden de script: sin esto, una
    excepción en un tab corta el script y deja EN BLANCO todos los tabs que se
    renderizan después (ya pasó dos veces: Graphs tumbó a Mapa y plantas, y el
    sandbox tumbó a las plantas). Con esto, el tab que falla muestra su
    traceback adentro y los demás siguen funcionando.
    """
    try:
        fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - queremos aislar CUALQUIER fallo de UI
        st.error(f"Este tab falló: {type(e).__name__}: {e}. "
                 "Los demás tabs siguen funcionando.")
        st.exception(e)


with tab_graphs:
    _render_seguro("Graphs", panel_graphs, resultados,
                   serie=st.session_state.get("serie"),
                   fallos=st.session_state.get("serie_fallos"),
                   unidad=unidad_volumen)

with tab_tablas:
    _render_seguro("Tablas totales", panel_tablas, resultados)

with tab_red:
    _render_seguro("Mapa de la red", panel_mapa, resultados)

with tab_sandbox:
    # El sandbox RE-MODELA la fisica desde comunes: necesita el original STD.
    st.caption("El sandbox re-modela la física y trabaja siempre en "
               "**MMm³/d STD**, independiente del selector de unidad.")
    _render_seguro("Plantas (sandbox)", panel_tab_plantas, resultados_fisicos,
                   resultados_fisicos.get("params_efectivos", PARAMS), FACTOR_MM)


def _mostrar_planta_contenido(nombre_planta, datos):
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


for _tab, _nombre in ((tab_tbx, "TTY - TBX"), (tab_dp, "TTY - Dew Point"),
                      (tab_mega, "MEGA")):
    with _tab:
        _render_seguro(_nombre, _mostrar_planta_contenido, _nombre, plantas[_nombre])
