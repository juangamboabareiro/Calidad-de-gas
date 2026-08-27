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

from ui.compat import ancho, arrow_safe

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

# Scope del rerun. Cuando el editor corre adentro de un `st.fragment`, hay que
# pedir `scope="fragment"` o Streamlit rerunea el script ENTERO y se pierde toda
# la ventaja: volveria a dibujar los otros siete tabs por cada checkbox.
_SCOPE = "app"

# Mensajes que tienen que sobrevivir a un rerun. Sin esto, un `st.success`
# seguido de `_rerun()` no se llega a dibujar nunca: el rerun descarta todo lo
# renderizado y el usuario ve que aprieta el boton y "no pasa nada".
CLAVE_FLASH = "plantas_flash"


def _flash(tipo: str, texto: str):
    """Deja un mensaje para mostrar DESPUES del rerun."""
    st.session_state[CLAVE_FLASH] = (tipo, texto)


def _mostrar_flash():
    mensaje = st.session_state.pop(CLAVE_FLASH, None)
    if not mensaje:
        return
    tipo, texto = mensaje
    getattr(st, tipo, st.info)(texto)


def configurar_scope(scope: str):
    """La llama el tab para avisar que estamos adentro de un fragment."""
    global _SCOPE
    _SCOPE = scope


def _rerun():
    if _SCOPE == "fragment":
        try:
            st.rerun(scope="fragment")
        except TypeError:
            # Streamlit viejo, sin `scope`. `st.rerun` levanta una excepcion de
            # control que hereda de BaseException, asi que este `except TypeError`
            # no se la come: solo atrapa la firma incompatible.
            pass
    st.rerun()


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
        subidas = cromas.get(planta.nombre)
        if subidas is not None:
            # Solo pisa si hay cromas para ESA planta en el buffer del uploader.
            # Si no, se respetan las que hubieran venido dentro de un escenario.
            planta.cromas_extra = subidas

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

    st.markdown("### Plantas y conexiones")

    _mostrar_flash()

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
    with st.expander("Agregar o eliminar plantas", expanded=not registro):
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

        if col_b.button("Crear", **ancho(), key="btn_crear"):
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
                    _rerun()

        borrables = [n for n, p in registro.items() if not p.es_base]
        if borrables:
            col_c, col_d = st.columns([2, 1])
            a_borrar = col_c.selectbox("Eliminar", borrables, key="borrar_sel")
            if col_d.button("Eliminar", **ancho(), key="btn_borrar"):
                # Hay que limpiar las conexiones que apuntaban a la planta
                # borrada, si no el registro queda con un destino fantasma y la
                # validacion lo marca como error.
                del registro[a_borrar]
                for p in registro.values():
                    p.conexiones = [c for c in p.conexiones if c.destino != a_borrar]
                _rerun()
        else:
            st.caption("Las tres plantas base no se pueden eliminar.")


def panel_escenarios(registro):
    """Cargar y guardar escenarios enteros: plantas Y gasoductos.

    Armar una planta a mano son ~20 interacciones, y cada una es un rerun. Un
    escenario prearmado la deja lista de un click, con cromatografias y con las
    intervenciones sobre ductos incluidas.
    """
    import json as _json

    from ui.escenarios import serializar, partir, resumen as resumen_escenario
    # Por la frontera unica: `ui.gasoductos_editor` no falla aunque el paquete
    # `pipeline.gasoductos` no este instalado.
    from ui.gasoductos_editor import obtener_intervenciones, Intervencion

    st.markdown("### Escenarios")
    st.caption(
        "Un escenario guarda **las plantas y los gasoductos juntos**: es una "
        "pregunta completa, no se parte en dos archivos.")

    _mostrar_flash()

    def _aplicar(datos, etiqueta):
        plantas_json, ductos_json = partir(datos)

        nuevas, parcheadas = aplicar_escenario(registro, plantas_json)

        # Las intervenciones se REEMPLAZAN enteras: son una lista ordenada y no
        # hay clave por la cual identificar "la misma" intervencion en dos
        # escenarios distintos. Mezclarlas no tiene un significado claro.
        intervenciones = obtener_intervenciones()
        intervenciones.clear()

        if ductos_json and Intervencion is None:
            # El escenario trae ductos pero el paquete no esta instalado. Se
            # avisa en vez de reventar: las plantas del escenario ya se
            # aplicaron y perderlas por esto seria peor.
            st.warning(
                f"El escenario trae {len(ductos_json)} intervención(es) sobre "
                "ductos, pero falta `pipeline/gasoductos/`. Se cargaron sólo "
                "las plantas.")
        elif ductos_json:
            intervenciones.extend(Intervencion.desde_dict(d) for d in ductos_json)

        # Las cromas del escenario vienen adentro de cada planta. Hay que
        # limpiar el buffer del uploader: si no, `_aplicar_cromas` se las pisa
        # con una lista vacia en el proximo rerun.
        st.session_state[CLAVE_CROMAS] = {}

        _flash("success",
               f"**{etiqueta}**: {resumen_escenario(plantas_json, ductos_json)}. "
               "Dale a **Resolver cascada**.")

    disponibles = _escenarios_disponibles()
    if disponibles:
        col_a, col_b = st.columns([2, 1])
        elegido = col_a.selectbox(
            "Escenario prearmado", list(disponibles), key="esc_sel",
            label_visibility="collapsed")
        if col_b.button("Cargar", **ancho(), key="btn_esc_load"):
            with st.status(f"Aplicando **{elegido}**…", expanded=False) as estado:
                try:
                    with open(disponibles[elegido], encoding="utf-8") as fh:
                        _aplicar(_json.load(fh), elegido)
                except Exception as e:
                    estado.update(label="No se pudo cargar", state="error")
                    st.error(f"{type(e).__name__}: {e}")
                else:
                    estado.update(label="Escenario aplicado ✅", state="complete")
                    _rerun()

    subido = st.file_uploader(
        "…o subí un escenario (.json)", type=["json"], key="esc_up")
    if subido is not None and st.button(
            f"Aplicar «{subido.name}»", **ancho(), key="btn_esc_up"):
        with st.status(f"Aplicando **{subido.name}**…", expanded=False) as estado:
            try:
                _aplicar(_json.loads(subido.getvalue().decode("utf-8")), subido.name)
            except Exception as e:
                estado.update(label="El archivo no es un escenario válido",
                              state="error")
                st.error(f"{type(e).__name__}: {e}")
            else:
                estado.update(label="Escenario aplicado ✅", state="complete")
                _rerun()

    col_e, col_f = st.columns(2)
    if col_e.button("Guardar", **ancho(), key="btn_guardar_reg"):
        ruta = guardar_registro(registro)
        st.success(f"Plantas guardadas en `{ruta}`.")

    col_f.download_button(
        "Descargar", **ancho(), key="btn_desc_reg",
        data=serializar(registro, obtener_intervenciones()).encode("utf-8"),
        file_name="escenario.json", mime="application/json",
        help="Plantas y gasoductos juntos.")


def aplicar_escenario(registro: dict, datos: list) -> tuple[int, int]:
    """Mezcla las plantas de un escenario sobre el registro. Devuelve (nuevas, parcheadas).

    MERGE, no reemplazo. Si reemplazara, cargar un escenario con una sola planta
    se llevaria puestas las tres base, que se siembran desde los parametros de
    la sidebar. Las plantas del archivo se agregan o pisan por nombre; las que
    no estan en el archivo quedan como estaban.

    Una entrada con `"solo_conexiones": true` es un PARCHE: se le aplican al
    registro existente unicamente `conexiones` y `deriva`, sin tocar capacidades
    ni retenidos. Sirve para que un escenario pueda enganchar su planta nueva a
    la cascada sin congelar las capacidades de las base con las de otra corrida.
    """
    from pipeline.plantas.registro import PlantaConfig, ConexionSalida

    nuevas = parcheadas = 0

    for d in datos:
        nombre = d["nombre"]

        if d.get("solo_conexiones") and nombre in registro:
            registro[nombre].conexiones = [
                ConexionSalida.desde_dict(c) for c in d.get("conexiones", [])]
            registro[nombre].deriva = bool(d.get("deriva", True))
            parcheadas += 1
            continue

        registro[nombre] = PlantaConfig.desde_dict(d)
        nuevas += 1

    return nuevas, parcheadas


def _escenarios_disponibles() -> dict:
    """{nombre visible: ruta} de los .json en `escenarios/`."""
    from pathlib import Path
    carpeta = Path("escenarios")
    if not carpeta.is_dir():
        return {}
    return {p.stem.replace("_", " ").title(): str(p)
            for p in sorted(carpeta.glob("*.json"))}


def _bloque_cromas(compuestos, factor_mm):
    with st.expander("Cromatografías de planta (archivo aparte)"):
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
                    **ancho(), hide_index=True)

        if st.session_state.get(CLAVE_CROMAS) and st.button(
                "Descartar cromatografías cargadas", key="btn_limpiar_cromas"):
            st.session_state[CLAVE_CROMAS] = {}
            _rerun()


def _bloque_general(planta: PlantaConfig, factor_mm):
    with st.expander(f"Parámetros de {planta.nombre}", expanded=True):
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
    with st.expander(f"Retención por compuesto — {planta.nombre}"):
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
            editable, hide_index=True, **ancho(),
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
            _rerun()
        copiable = [n for n in obtener_registro() if n != planta.nombre]
        if copiable:
            origen = col_b.selectbox(
                "Copiar de", copiable, key=f"cop_{planta.nombre}",
                label_visibility="collapsed")
            if col_b.button("Copiar retención", key=f"btncop_{planta.nombre}"):
                otra = obtener_registro()[origen].retenidos
                if otra is not None:
                    planta.retenidos = otra.copy()
                    _rerun()


def _bloque_conexiones(planta: PlantaConfig, registro, factor_mm):
    """Esquema de proporciones: a dónde va el sobrante y en qué reparto."""
    with st.expander(f"Conexiones de salida — {planta.nombre}", expanded=True):
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
            pd.DataFrame(filas), hide_index=True, **ancho(),
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
