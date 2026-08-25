"""
Panel de configuracion de plantas.
==================================

Tres cosas, en tres expanders:

  1. Agregar / eliminar plantas.
  2. Editar la retencion por compuesto (el "esquema MEGA": un porcentaje por
     compuesto, sin correcciones piecewise).
  3. Editar la logica de conexion: a que planta va el sobrante, en que
     proporcion y con que tope.

Mas la carga del archivo APARTE de cromatografias, que no toca `inputs.xlsx`.

Uso en app.py:

    from ui.plantas_editor import panel_plantas, obtener_registro

    with st.sidebar:
        panel_plantas(retenidos_rtp, ctes.COMPUESTOS, config)
    registro = obtener_registro()

El registro vive en `st.session_state['registro_plantas']` para sobrevivir a los
reruns de Streamlit. Se puede guardar a `datos/plantas.json` y recuperar, asi un
escenario armado no se pierde al recargar la pagina.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pipeline.plantas.registro import (
    PRESETS,
    PlantaConfig,
    ConexionSalida,
    registro_base,
    crear_planta,
    validar_registro,
    guardar_registro,
    cargar_registro,
    INFINITO,
)
from io_.cromatografias_planta import cargar_cromas_extra, resumen as resumen_cromas


CLAVE = "registro_plantas"
CLAVE_CROMAS = "cromas_extra_por_planta"


# ===========================================================================
# Estado
# ===========================================================================

def inicializar(retenidos_rtp, compuestos, config, tbx_en_servicio: bool, forzar=False):
    """Arranca el registro con las tres plantas de siempre.

    `forzar=True` lo resetea: se usa cuando cambia el excel de inputs o la fecha
    de PM, porque los retenidos y el estado de TBX salen de ahi.
    """
    if forzar or CLAVE not in st.session_state:
        st.session_state[CLAVE] = registro_base(
            config, retenidos_rtp, compuestos, tbx_en_servicio)
    else:
        # Las base siguen a la fecha de PM aunque el usuario haya tocado otras.
        base = st.session_state[CLAVE].get("TTY - TBX")
        if base is not None and base.es_base:
            base.activa = tbx_en_servicio

    st.session_state.setdefault(CLAVE_CROMAS, {})
    return st.session_state[CLAVE]


def obtener_registro() -> dict[str, PlantaConfig]:
    return st.session_state.get(CLAVE, {})


def _aplicar_cromas():
    """Pega las cromatografias cargadas sobre las plantas del registro.

    Se hace en un paso aparte y no al subir el archivo porque el usuario puede
    subir las cromas ANTES de crear la planta, o crear la planta despues. Cada
    rerun reconcilia las dos cosas.
    """
    registro = obtener_registro()
    cromas = st.session_state.get(CLAVE_CROMAS, {})
    huerfanas = []

    for planta in registro.values():
        planta.cromas_extra = cromas.get(planta.nombre, [])

    for nombre in cromas:
        if nombre not in registro:
            huerfanas.append(nombre)

    return huerfanas


# ===========================================================================
# Panel
# ===========================================================================

def panel_plantas(retenidos_rtp, compuestos, config, tbx_en_servicio: bool,
                  factor_mm=1000.0):
    """Dibuja el panel completo y devuelve (registro, errores, avisos)."""

    inicializar(retenidos_rtp, compuestos, config, tbx_en_servicio)
    registro = obtener_registro()

    st.markdown("### 🏭 Plantas y conexiones")

    _bloque_alta(registro, compuestos)
    _bloque_cromas(compuestos, factor_mm)
    huerfanas = _aplicar_cromas()

    if huerfanas:
        st.warning(
            "Hay cromatografias cargadas para plantas que no existen en el "
            f"registro: {', '.join(huerfanas)}. Creá la planta con ese nombre "
            "exacto o corregí la columna `Planta` del archivo.")

    if not registro:
        st.info("No hay plantas configuradas.")
        return registro, ["Registro vacio."], []

    seleccion = st.selectbox("Planta a editar", sorted(registro), key="planta_sel")
    planta = registro[seleccion]

    _bloque_general(planta, factor_mm)
    _bloque_retenidos(planta, compuestos)
    _bloque_conexiones(planta, registro, factor_mm)

    errores, avisos = validar_registro(registro)
    _bloque_estado(registro, errores, avisos)

    return registro, errores, avisos


# ---------------------------------------------------------------------------

def _bloque_alta(registro, compuestos):
    with st.expander("➕ Agregar o eliminar plantas", expanded=not registro):
        col_a, col_b = st.columns([2, 1])
        nombre = col_a.text_input("Nombre de la planta nueva", key="nueva_nombre")
        preset = col_a.selectbox(
            "Arrancar con las features de…", list(PRESETS), key="nueva_preset",
            help="El modelo es uno solo. El preset sólo carga los valores "
                 "iniciales de las features; después se pueden cambiar todas.")
        st.caption(_describir_preset(preset))
        pool = col_a.text_input(
            "Nombre de pool (columna `Gasoducto`)",
            key="nueva_pool",
            help="Con qué valor de `Gasoducto` se filtra el gas que entra. "
                 "Dejalo igual al nombre si la planta es un destino nuevo, o "
                 "poné el de otra planta si son dos trenes sobre el mismo gas "
                 "(el caso TTY-TBX / TTY-Dew Point).")

        if col_b.button("Crear", use_container_width=True, key="btn_crear"):
            nombre = (nombre or "").strip()
            if not nombre:
                st.error("Poné un nombre.")
            elif nombre in registro:
                st.error(f"Ya existe una planta llamada '{nombre}'.")
            else:
                try:
                    registro[nombre] = crear_planta(
                        nombre, preset=preset, compuestos=compuestos,
                        nombre_pool=(pool or "").strip() or None)
                except ValueError as e:
                    st.error(str(e))
                else:
                    st.success(
                        f"'{nombre}' creada con las features de {preset}. "
                        "Arranca sin retención, sin capacidades y sin "
                        "conexiones: cargale los retenidos y decidí a dónde "
                        "manda el sobrante.")
                    st.rerun()

        borrables = [n for n, p in registro.items() if not p.es_base]
        if borrables:
            col_c, col_d = st.columns([2, 1])
            a_borrar = col_c.selectbox("Eliminar", borrables, key="borrar_sel")
            if col_d.button("Eliminar", use_container_width=True, key="btn_borrar"):
                # Hay que limpiar las conexiones que apuntaban a la planta
                # borrada, si no el registro queda con un destino fantasma y la
                # validacion lo marca como error.
                del registro[a_borrar]
                for p in registro.values():
                    p.conexiones = [c for c in p.conexiones if c.destino != a_borrar]
                st.rerun()
        else:
            st.caption("Las tres plantas base no se pueden eliminar.")

        col_e, col_f = st.columns(2)
        if col_e.button("💾 Guardar escenario", use_container_width=True, key="btn_guardar_reg"):
            ruta = guardar_registro(registro)
            st.success(f"Guardado en `{ruta}`.")
        if col_f.button("📂 Cargar escenario", use_container_width=True, key="btn_cargar_reg"):
            try:
                st.session_state[CLAVE] = cargar_registro()
                st.rerun()
            except FileNotFoundError:
                st.error("No hay `datos/plantas.json` guardado todavía.")


def _bloque_cromas(compuestos, factor_mm):
    with st.expander("📄 Cromatografías de planta (archivo aparte)"):
        st.caption(
            "Va **separado de `inputs.xlsx`**. Una fila por corriente, con las "
            "columnas `Planta`, `Origen`, `Volumen` (MMm3/d) y una columna por "
            "compuesto. Se suma al pool antes de calcular la mezcla, así que "
            "pesa en `gas_rico_IN` igual que el gas que llega por gasoducto.")

        archivo = st.file_uploader(
            "Archivo de cromatografías", type=["xlsx", "xlsm", "csv"],
            key="up_cromas")

        if archivo is not None:
            cromas, avisos = cargar_cromas_extra(
                archivo, compuestos, factor_volumen=factor_mm)
            st.session_state[CLAVE_CROMAS] = cromas

            for aviso in avisos:
                st.warning(aviso)

            if cromas:
                st.dataframe(
                    resumen_cromas(cromas, factor_mm).style.format({
                        "Volumen [MMm3/d]": "{:,.2f}", "Suma molar": "{:,.4f}"}),
                    use_container_width=True, hide_index=True)

        if st.session_state.get(CLAVE_CROMAS) and st.button(
                "Descartar cromatografías cargadas", key="btn_limpiar_cromas"):
            st.session_state[CLAVE_CROMAS] = {}
            st.rerun()


def _bloque_general(planta: PlantaConfig, factor_mm):
    with st.expander(f"⚙️ Parámetros de {planta.nombre}", expanded=True):
        planta.activa = st.checkbox(
            "En servicio", value=planta.activa, key=f"act_{planta.nombre}",
            help="Fuera de servicio no trata nada y deja pasar todo el gas.")

        col_a, col_b = st.columns(2)

        cap_evac = col_a.number_input(
            "Capacidad de evacuación de LGN [tn/d]",
            value=(0.0 if planta.capacidad_evacuacion == INFINITO
                   else float(planta.capacidad_evacuacion)),
            min_value=0.0, step=100.0, key=f"evac_{planta.nombre}",
            help="Es la restricción activa del modelo. 0 = sin límite.")
        planta.capacidad_evacuacion = INFINITO if cap_evac == 0 else cap_evac

        cap_ing = col_b.number_input(
            "Capacidad de ingreso de gas [MMm3/d]",
            value=(0.0 if planta.capacidad_ingreso is None
                   else float(planta.capacidad_ingreso) / factor_mm),
            min_value=0.0, step=1.0, key=f"ing_{planta.nombre}",
            help="0 = sin límite de ingreso.")
        planta.capacidad_ingreso = None if cap_ing == 0 else cap_ing * factor_mm

        planta.toma_volumen_del_pool = st.checkbox(
            "Toma el volumen de su propio pool (cabecera)",
            value=planta.toma_volumen_del_pool, key=f"cab_{planta.nombre}",
            help="Destildado, el volumen se lo pasa el eslabón anterior y el "
                 "pool sólo aporta la cromatografía. Es el caso de "
                 "TTY - Dew Point, que comparte el gas con TTY - TBX.")

        planta.nombre_pool = st.text_input(
            "Nombre de pool (columna `Gasoducto`)",
            value=planta.nombre_pool or planta.nombre,
            key=f"pool_{planta.nombre}",
            help="Con qué valor de `Gasoducto` se filtra el gas que entra. "
                 "Dos plantas con el MISMO nombre de pool son dos trenes sobre "
                 "el mismo gas, con cromatografía idéntica.") or planta.nombre

        planta.color = st.color_picker(
            "Color en el diagrama", value=planta.color, key=f"col_{planta.nombre}")


def _describir_preset(preset: str) -> str:
    f = PRESETS[preset]
    partes = [
        "deriva el sobrante" if f["deriva"] else "**terminal** (todo el sobrante es bypass)",
        "en servicio" if f["activa"] else "fuera de servicio",
        "cabecera de su pool" if f["toma_volumen_del_pool"]
        else "recibe el volumen del tren anterior",
    ]
    return "→ " + " · ".join(partes)


def _bloque_retenidos(planta: PlantaConfig, compuestos):
    """Retención por compuesto, en %.

    Es el esquema plano de MEGA: una fracción fija por compuesto, aplicada como
    `gas_residual_OUT = gas_rico_IN * (1 - retenidos)`. TTY-DP y TTY-TBX además
    recalculan coeficientes cuando se pasan del tope de tn/d; esa corrección
    vive en TTY.py y no se toca desde acá.
    """
    with st.expander(f"🧪 Retención por compuesto — {planta.nombre}"):
        st.caption(
            "Porcentaje de cada compuesto que la planta retiene como líquido. "
            "El resto sale en el gas residual.")

        if planta.retenidos is None:
            planta.retenidos = pd.DataFrame([{c: 0.0 for c in compuestos}])

        actual = planta.retenidos.iloc[0].reindex(list(compuestos)).fillna(0.0)

        editable = pd.DataFrame({
            "Compuesto": list(compuestos),
            "Retención [%]": (actual.astype(float) * 100).values,
        })

        editado = st.data_editor(
            editable, hide_index=True, use_container_width=True,
            key=f"ret_{planta.nombre}",
            column_config={
                "Compuesto": st.column_config.TextColumn(disabled=True),
                "Retención [%]": st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=0.1, format="%.2f"),
            },
        )

        planta.retenidos = pd.DataFrame([{
            fila["Compuesto"]: float(fila["Retención [%]"]) / 100.0
            for _, fila in editado.iterrows()
        }])

        col_a, col_b = st.columns(2)
        if col_a.button("Todo en 0", key=f"ret0_{planta.nombre}"):
            planta.retenidos = pd.DataFrame([{c: 0.0 for c in compuestos}])
            st.rerun()
        copiable = [n for n in obtener_registro() if n != planta.nombre]
        if copiable:
            origen = col_b.selectbox(
                "Copiar de", copiable, key=f"cop_{planta.nombre}",
                label_visibility="collapsed")
            if col_b.button("Copiar retención", key=f"btncop_{planta.nombre}"):
                otra = obtener_registro()[origen].retenidos
                if otra is not None:
                    planta.retenidos = otra.copy()
                    st.rerun()


def _bloque_conexiones(planta: PlantaConfig, registro, factor_mm):
    """Esquema de proporciones: a dónde va el sobrante y en qué reparto."""
    with st.expander(f"🔀 Conexiones de salida — {planta.nombre}", expanded=True):
        st.caption(
            "La planta se llena hasta su capacidad; **el sobrante** se reparte "
            "entre estos destinos según el porcentaje. Lo que no se lleva "
            "nadie (o excede el tope de una rama) es bypass.")

        planta.deriva = st.checkbox(
            "Deriva el sobrante a otra planta",
            value=planta.deriva, key=f"der_{planta.nombre}",
            help="Destildado, la planta se comporta como último eslabón: trata "
                 "lo que puede y TODO el sobrante va a bypass. Las conexiones "
                 "de abajo quedan guardadas para poder volver a prenderlas.")

        if not planta.deriva:
            st.info(
                "Sin derivación: todo el sobrante es bypass. "
                "El bypass no se puede apagar — es a dónde va el gas que "
                "ninguna planta pudo tratar ni recibir, y sacarlo lo haría "
                "desaparecer del balance.")

        candidatos = [n for n in sorted(registro) if n != planta.nombre]
        if not candidatos:
            st.info("No hay otras plantas a las que conectar.")
            return

        actuales = {c.destino: c for c in planta.conexiones}

        filas = []
        for destino in candidatos:
            c = actuales.get(destino)
            filas.append({
                "Destino": destino,
                "Conectar": c is not None,
                "% del sobrante": (c.proporcion * 100) if c else 0.0,
                "Tope [MMm3/d]": (
                    0.0 if c is None or c.tope == INFINITO else c.tope / factor_mm),
                "Mismo pool": bool(c.comparte_pool) if c else False,
            })

        editado = st.data_editor(
            pd.DataFrame(filas), hide_index=True, use_container_width=True,
            key=f"con_{planta.nombre}", disabled=not planta.deriva,
            column_config={
                "Destino": st.column_config.TextColumn(disabled=True),
                "Conectar": st.column_config.CheckboxColumn(),
                "% del sobrante": st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=5.0, format="%.1f"),
                "Tope [MMm3/d]": st.column_config.NumberColumn(
                    min_value=0.0, step=1.0, format="%.2f",
                    help="0 = sin tope."),
                "Mismo pool": st.column_config.CheckboxColumn(
                    help="Tildado: son dos trenes sobre el mismo gas, la "
                         "cromatografía no cambia y sólo se pasa volumen. "
                         "Destildado: derivación real, el gas entra a un pool "
                         "de otra composición y pesa en la mezcla."),
            },
        )

        nuevas = []
        for _, fila in editado.iterrows():
            if not fila["Conectar"]:
                continue
            tope = float(fila["Tope [MMm3/d]"])
            nuevas.append(ConexionSalida(
                destino=str(fila["Destino"]),
                proporcion=float(fila["% del sobrante"]) / 100.0,
                tope=INFINITO if tope <= 0 else tope * factor_mm,
                comparte_pool=bool(fila["Mismo pool"]),
            ))
        planta.conexiones = nuevas

        suma = sum(c.proporcion for c in nuevas)
        if not planta.deriva:
            pass
        elif nuevas:
            if abs(suma - 1.0) < 1e-9:
                st.caption("✅ El sobrante se reparte entero entre los destinos.")
            elif suma < 1.0:
                st.caption(
                    f"ℹ️ Se reparte el {suma:.0%} del sobrante; el "
                    f"{1 - suma:.0%} restante va a bypass.")
            else:
                st.caption(
                    f"⚠️ Las proporciones suman {suma:.0%}: se renormalizan "
                    "a 100% del sobrante.")
        else:
            st.caption("Sin conexiones: todo el sobrante es bypass.")


def _bloque_estado(registro, errores, avisos):
    if errores:
        st.error("**No se puede correr la cascada así:**\n\n"
                 + "\n".join(f"- {e}" for e in errores))
    for aviso in avisos:
        st.warning(aviso)
    if not errores and not avisos:
        st.success(f"Configuración válida: {len(registro)} plantas.")
