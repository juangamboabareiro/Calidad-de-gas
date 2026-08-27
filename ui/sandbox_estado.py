"""
Estado del sandbox: un solo lugar que sabe qué guardar y cómo borrarlo.
======================================================================

Por qué existe este archivo
---------------------------
El sandbox guarda estado en seis claves de `session_state` repartidas en tres
módulos. "Restablecer" tiene que limpiarlas TODAS, y si cada módulo se limpiara
solo, alcanza con que uno se olvide para dejar el sandbox en un estado a medias
— con el registro nuevo y el resultado viejo en pantalla, por ejemplo.

LA TRAMPA DE LOS WIDGETS
------------------------
Borrar las claves de datos NO alcanza, y esto es lo que hace que un botón sea
mejor que "borralo a mano".

Streamlit guarda el valor de cada widget en `session_state` bajo su `key`. Una
vez que la clave existe, `st.number_input(value=X, key="evac_MEGA")` devuelve
el valor GUARDADO y no `X`. Entonces:

    1. el usuario cambia la capacidad de MEGA de 5600 a 900
    2. se resiembra el registro desde los parámetros de la sidebar (5600)
    3. en el rerun, el widget `evac_MEGA` devuelve 900 y lo escribe encima

...y el reset parece no haber hecho nada. Hay que borrar también las claves de
los widgets, que es lo que hace `PREFIJOS_WIDGETS`.

Si se agrega un widget nuevo al editor con un prefijo que no esté en esa lista,
su valor va a sobrevivir al reset. Es el único punto que hay que mantener.
"""

from __future__ import annotations

import streamlit as st

from ui.compat import ancho


# Claves de DATOS: el registro, las intervenciones, los resultados.
CLAVES_DATOS = (
    "registro_plantas",             # plantas_editor
    "cromas_extra_por_planta",      # plantas_editor (buffer del uploader)
    "intervenciones_gasoductos",    # gasoductos_editor
    "sandbox_resultado",            # tab_plantas
    "sandbox_informe_ductos",       # tab_plantas
    "sandbox_red_gasoductos",       # tab_plantas -> mapa
    "plantas_flash",                # mensajes pendientes
    "gasoductos_flash",
)

# `sandbox_flujos_oficiales` NO va en CLAVES_DATOS: es un cache de la corrida
# OFICIAL, no algo que el usuario configuro. Borrarlo dejaria el bloque de
# control y el impacto sin referencia hasta el proximo rerun del tab.

# Prefijos de las claves de WIDGET. Ver "LA TRAMPA DE LOS WIDGETS" arriba.
PREFIJOS_WIDGETS = (
    # plantas_editor
    "act_", "evac_", "ing_", "cab_", "pool_", "col_",
    "ret_", "ret0_", "cop_", "btncop_", "con_", "der_",
    "planta_sel", "nueva_", "borrar_sel", "esc_",
    # Botones y uploaders del editor. `up_cromas` es el que importa: es el
    # file_uploader, y si su clave sobrevive el archivo subido se vuelve a
    # aplicar despues del reset.
    "up_cromas", "btn_crear", "btn_borrar", "btn_guardar_reg", "btn_desc_reg",
    "btn_esc_load", "btn_esc_up", "btn_limpiar_cromas", "btn_correr_sandbox",
    "btn_bajar_sim",
    # gasoductos_editor
    "gd_",
)

CLAVE_CONFIRMAR = "sandbox_confirmar_reset"


def restablecer() -> int:
    """Borra todo el estado del sandbox. Devuelve cuántas claves limpió.

    No resiembra nada: al desaparecer `registro_plantas`, `inicializar` lo
    vuelve a armar desde los parámetros de la sidebar en el próximo rerun. Así
    el reset siempre deja el sandbox como recién abierto, sin una segunda copia
    de la lógica de siembra que se pueda desincronizar.
    """
    a_borrar = [c for c in CLAVES_DATOS if c in st.session_state]

    a_borrar += [
        clave for clave in list(st.session_state)
        if isinstance(clave, str) and clave.startswith(PREFIJOS_WIDGETS)
    ]

    for clave in set(a_borrar):
        st.session_state.pop(clave, None)

    st.session_state.pop(CLAVE_CONFIRMAR, None)

    return len(set(a_borrar))


def hay_algo_que_restablecer() -> bool:
    """True si el usuario tocó algo. Sirve para no ofrecer un reset inútil."""
    if any(c in st.session_state for c in CLAVES_DATOS[2:]):
        return True

    intervenciones = st.session_state.get("intervenciones_gasoductos") or []
    if intervenciones:
        return True

    registro = st.session_state.get("registro_plantas") or {}
    if any(not p.es_base for p in registro.values()):
        return True

    # Un widget tocado es el caso silencioso: el usuario cambió una capacidad
    # de una planta base y el registro sigue teniendo las tres de siempre.
    return any(
        isinstance(c, str) and c.startswith(PREFIJOS_WIDGETS)
        for c in st.session_state
    )


def boton_restablecer(rerun, etiqueta="Restablecer el sandbox") -> bool:
    """Botón con confirmación en dos pasos. Devuelve True si restableció.

    La confirmación no es un adorno: un clic accidental puede borrar veinte
    minutos de configuración, y el usuario al que esto apunta no sabe que puede
    volver a cargar un escenario guardado. Dos clics es el precio más barato
    posible por evitarlo.

    Parameters
    ----------
    rerun : callable
        La función de rerun del módulo que llama, para que respete el scope del
        fragment. Un `st.rerun()` de app entero acá redibuja los ocho tabs.
    """
    if st.session_state.get(CLAVE_CONFIRMAR):
        st.warning(
            "**¿Restablecer?** Se borran las plantas que agregaste, las "
            "intervenciones sobre ductos y los cambios de capacidad. Vuelve "
            "todo a la corrida oficial.\n\n"
            "Si querés conservar esto, cancelá y usá **Descargar** para "
            "guardarlo como escenario.")

        col_si, col_no = st.columns(2)

        if col_si.button("Sí, restablecer", type="primary",
                         **ancho(), key="btn_reset_si"):
            cantidad = restablecer()
            st.session_state["plantas_flash"] = (
                "success", f"Sandbox restablecido ({cantidad} valores borrados).")
            rerun()
            return True

        if col_no.button("Cancelar", **ancho(), key="btn_reset_no"):
            st.session_state.pop(CLAVE_CONFIRMAR, None)
            rerun()

        return False

    if st.button(etiqueta, **ancho(), key="btn_reset",
                 help="Vuelve el sandbox al estado inicial: las tres plantas "
                      "de siempre y ningún ducto intervenido."):
        st.session_state[CLAVE_CONFIRMAR] = True
        rerun()

    return False
