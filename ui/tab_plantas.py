"""
Tab "Plantas (sandbox)" para app.py.
====================================

Un tab aparte que corre su PROPIA cascada sobre el registro editable, sin tocar
el pipeline de produccion. El resto del tablero sigue funcionando exactamente
como esta: mismos modulos, mismos numeros, mismo codigo.

POR QUE UN SANDBOX Y NO UN REEMPLAZO
------------------------------------
El pipeline actual esta validado contra el Excel. Cambiarlo para poder agregar
plantas obliga a revalidar todo de una. Con un tab aparte, el escenario nuevo se
arma y se mira al lado del oficial, y recien cuando los numeros convencen se
decide si reemplaza a la cascada hardcodeada.

EL CONTROL
----------
Antes de agregar nada, el registro arranca siendo las tres plantas de siempre
con los parametros de la sidebar. Entonces su resultado TIENE que dar igual al
del tab "Reparto del gas". El bloque de control compara las dos tablas planta
por planta y muestra el desvio.

Si el control da distinto de cero con el registro sin tocar, hay un bug en esta
capa y no hay que creerle a ningun escenario que se arme encima. Es el primer
numero que hay que mirar.

QUE NECESITA DE app.py
----------------------
    resultados["comunes"]        el dict que ya se arma en `ejecutar_pipeline`
    resultados["retenidos_rtp"]  para sembrar los retenidos de las base
    resultados["flujos_plantas"] para el control (ya existe)

Las dos primeras son una linea cada una en `ejecutar_pipeline`.
"""

import pandas as pd
import streamlit as st

from pipeline.plantas.cascada import resolver_cascada, dot_cascada, desvio_balance
from ui.plantas_editor import panel_plantas, obtener_registro


CLAVE_RESULTADO = "sandbox_resultado"

COLUMNAS_VOLUMEN = ["vol_disponible", "vol_maximo", "vol_asignado",
                    "sobrante", "vol_derivado", "bypass"]

# Nombres con los que las tres plantas base aparecen en la tabla de produccion.
# Si en app.py se renombran, hay que tocar esto o el control queda vacio.
BASE = ["TTY - TBX", "TTY - Dew Point", "MEGA"]


def panel_tab_plantas(resultados, params, factor_mm=1000.0):
    """Dibuja el tab completo.

    Parameters
    ----------
    resultados : dict
        Lo que devuelve `ejecutar_pipeline`, con `comunes` y `retenidos_rtp`.
    params : dict | module
        Las capacidades y topes de la sidebar. `registro_base` acepta los dos.
    """

    st.subheader("Plantas (sandbox)")
    st.caption(
        "Cascada configurable, **independiente del resto del tablero**. Lo que "
        "se arme acá no afecta a los otros tabs: corre su propio modelo sobre "
        "el mismo pool de gas.")

    faltantes = [k for k in ("comunes", "retenidos_rtp") if k not in resultados]
    if faltantes:
        st.error(
            f"Falta `{'`, `'.join(faltantes)}` en los resultados del pipeline. "
            "Agregá en `ejecutar_pipeline`, antes del `return`:\n\n"
            "```python\n"
            'resultados["comunes"] = comunes\n'
            'resultados["retenidos_rtp"] = retenidos_rtp\n'
            "```")
        return

    comunes = resultados["comunes"]
    retenidos_rtp = resultados["retenidos_rtp"]
    compuestos = comunes["COMPUESTOS"]
    tbx_en_servicio = bool(resultados.get("tbx_en_servicio", True))

    col_editor, col_salida = st.columns([2, 3], gap="large")

    with col_editor:
        registro, errores, _ = panel_plantas(
            retenidos_rtp=retenidos_rtp,
            compuestos=compuestos,
            config=params,
            tbx_en_servicio=tbx_en_servicio,
            factor_mm=factor_mm,
        )

        st.divider()
        correr = st.button(
            "▶️ Resolver cascada", type="primary", use_container_width=True,
            disabled=bool(errores), key="btn_correr_sandbox")

        if errores:
            st.caption("Corregí los errores de arriba para poder correr.")

    if correr:
        with st.spinner("Resolviendo…"):
            try:
                plantas, flujos = resolver_cascada(registro, comunes)
            except Exception as e:
                st.session_state.pop(CLAVE_RESULTADO, None)
                with col_salida:
                    st.error(f"La cascada falló: {type(e).__name__}: {e}")
                    st.exception(e)
                return
        st.session_state[CLAVE_RESULTADO] = (plantas, flujos)

    with col_salida:
        guardado = st.session_state.get(CLAVE_RESULTADO)
        if guardado is None:
            st.info("Configurá las plantas y dale a **Resolver cascada**.")
            return

        plantas, flujos = guardado
        _bloque_control(flujos, resultados.get("flujos_plantas"), factor_mm)
        _bloque_balance(flujos)
        _bloque_flujos(flujos, factor_mm)
        _bloque_grafo(obtener_registro(), plantas, factor_mm)
        _bloque_kpis(plantas, factor_mm)


# ===========================================================================
# Bloques
# ===========================================================================

def _bloque_control(flujos_sandbox, flujos_produccion, factor_mm):
    """Compara las tres plantas base contra la cascada oficial.

    Es el primer numero a mirar: con el registro sin tocar tiene que dar cero.
    Si no da cero, el bug esta en esta capa y no hay que creerle a nada de lo
    que se arme encima.
    """

    if flujos_produccion is None:
        return

    comunes_idx = [n for n in BASE
                   if n in flujos_sandbox.index and n in flujos_produccion.index]
    if not comunes_idx:
        st.info("No se puede comparar contra la cascada oficial: las plantas "
                "base fueron renombradas o eliminadas.")
        return

    columnas = [c for c in COLUMNAS_VOLUMEN + ["lgn_asignado"]
                if c in flujos_sandbox.columns and c in flujos_produccion.columns]

    delta = (flujos_sandbox.loc[comunes_idx, columnas].astype(float)
             - flujos_produccion.loc[comunes_idx, columnas].astype(float))
    peor = float(delta.abs().to_numpy().max())

    # Tolerancia relativa al tamaño de los volúmenes, no absoluta: 1e-6 sobre
    # decenas de miles es ruido de punto flotante, no una diferencia real.
    escala = max(float(flujos_produccion.loc[comunes_idx, columnas].abs().to_numpy().max()), 1.0)
    coincide = peor / escala < 1e-9

    modificado = len(flujos_sandbox.index) != len(comunes_idx)

    if coincide:
        st.success(
            f"✅ Control: las {len(comunes_idx)} plantas base dan **idéntico** "
            f"a la cascada oficial (desvío máx. {peor:.2e})."
            + (" Las plantas agregadas no las alteraron." if modificado else ""))
    else:
        # Distinguir los dos casos importa: si el registro está intacto, esto es
        # un bug; si el usuario cambió capacidades, es el resultado esperado.
        if modificado:
            razon = ("Puede ser esperable si les cambiaste capacidades, "
                     "conexiones o si las plantas agregadas les sacan gas.")
        else:
            razon = ("No hay plantas agregadas, así que si tampoco les tocaste "
                     "capacidades ni conexiones a las base, esto **no debería "
                     "pasar**: es un bug de esta capa, y no le creas a ningún "
                     "escenario que armes encima.")
        st.warning(
            f"⚠️ Control: las plantas base difieren de la cascada oficial en "
            f"hasta {peor / factor_mm:,.4f} (MMm3/d o tn/d según la columna). "
            + razon)
        with st.expander("Ver diferencias por planta"):
            vista = delta.copy()
            for c in [x for x in COLUMNAS_VOLUMEN if x in vista.columns]:
                vista[c] = vista[c] / factor_mm
            st.dataframe(vista.style.format("{:,.6f}"), use_container_width=True)


def _bloque_balance(flujos):
    desvio = desvio_balance(flujos)
    if desvio < 1e-6:
        st.caption(
            f"Balance por eslabón OK (desvío máx. {desvio:.2e}): "
            "`vol_disponible = vol_asignado + vol_derivado + bypass`.")
    else:
        st.error(
            f"El balance por eslabón no cierra: desvío máx. {desvio:,.4f}. "
            "Debería valer `vol_disponible = vol_asignado + vol_derivado + bypass`.")


def _bloque_flujos(flujos, factor_mm):
    st.markdown("**Reparto del gas**")
    st.caption(
        "Volúmenes en MMm3/d, LGN en tn/d. El `vol_derivado` de una planta es "
        "el `vol_disponible` de la siguiente, así que **no se pueden sumar las "
        "columnas entre plantas**.")

    vista = flujos.copy()
    for col in [c for c in COLUMNAS_VOLUMEN if c in vista.columns]:
        vista[col] = vista[col].astype(float) / factor_mm

    st.dataframe(
        vista.style.format({
            **{c: "{:,.2f}" for c in COLUMNAS_VOLUMEN if c in vista.columns},
            "lgn_unitario": "{:,.5f}", "lgn_asignado": "{:,.1f}",
        }),
        use_container_width=True,
    )

    csv = flujos.reset_index(names="Planta").to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Descargar flujos", csv, "flujos_sandbox.csv",
                       "text/csv", key="dl_sandbox")


def _bloque_grafo(registro, plantas, factor_mm):
    st.markdown("**Cascada**")
    st.caption(
        "Línea gruesa = derivación real (el gas entra a un pool de otra "
        "composición). Línea fina = mismo pool, sólo pasa volumen. "
        "Punteado = bypass. Valores en MMm3/d.")
    st.graphviz_chart(dot_cascada(registro, plantas, factor_mm),
                      use_container_width=True)


def _bloque_kpis(plantas, factor_mm):
    st.markdown("**Estado de cada planta**")

    for nombre, datos in plantas.items():
        flujos = datos["flujos"]
        etiqueta = nombre if flujos.get("activa", True) else f"{nombre} (fuera de servicio)"

        with st.expander(etiqueta, expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Gas disponible",
                      f"{flujos['vol_disponible'] / factor_mm:,.2f}",
                      help="MMm3/d que llegan a esta planta.")
            c2.metric("Gas tratado",
                      f"{flujos['vol_asignado'] / factor_mm:,.2f}",
                      help="MMm3/d que efectivamente trata.")
            c3.metric("LGN", f"{flujos.get('lgn_asignado', 0):,.1f}",
                      help="tn/d recuperados.")

            vmax = flujos.get("vol_maximo")
            ocupacion = (flujos["vol_asignado"] / vmax
                         if vmax and vmax not in (0, float("inf")) else None)
            c4.metric("Ocupación",
                      "—" if ocupacion is None else f"{ocupacion:.0%}",
                      help="vol_asignado / vol_maximo.")

            derivados = flujos.get("derivados") or {}
            if derivados:
                st.caption("Deriva a: " + " · ".join(
                    f"**{d}** {v / factor_mm:,.2f}" for d, v in derivados.items()
                    if v > 0) or "Deriva a: —")
            if flujos.get("bypass", 0) > 0:
                st.caption(f"Bypass: {flujos['bypass'] / factor_mm:,.2f} MMm3/d")

            cromas = datos["config"].cromas_extra
            if cromas:
                total = sum(c["vol_derivacion"] for c in cromas) / factor_mm
                st.caption(
                    f"Incluye {len(cromas)} cromatografía(s) cargadas a mano "
                    f"por {total:,.2f} MMm3/d.")

            with st.expander("Tabla de detalle"):
                st.caption(
                    "`Volumen_pool` es el gas del pool antes del reparto; "
                    "`Volumen_inyectado` es la porción asignada a esta planta.")
                st.dataframe(datos["tabla_total"], use_container_width=True)
