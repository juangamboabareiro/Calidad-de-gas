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
from ui.plantas_editor import panel_plantas, obtener_registro, configurar_scope
# El sub-panel de gasoductos se importa de forma tolerante A PROPOSITO.
#
# `app.py` importa este modulo a nivel de archivo, asi que un ImportError aca
# tumba el tablero ENTERO: los ocho tabs, incluidos los siete que no tienen nada
# que ver con el sandbox. Que falte un archivo opcional no puede dejar sin
# tablero a alguien que solo queria mirar el reparto del gas.
#
# Si el paquete `pipeline.gasoductos` no esta, el tab arranca igual y el
# sub-tab de ductos explica que falta y como resolverlo.
try:
    from ui.gasoductos_editor import (
        panel_gasoductos, obtener_intervenciones,
        configurar_scope as configurar_scope_gd,
    )
    from pipeline.gasoductos.intervenciones import aplicar_intervenciones
    GASODUCTOS_DISPONIBLE = True
    ERROR_GASODUCTOS = None
except ImportError as _e:
    GASODUCTOS_DISPONIBLE = False
    ERROR_GASODUCTOS = str(_e)

    def panel_gasoductos(*a, **k):
        return []

    def obtener_intervenciones():
        return []

    def configurar_scope_gd(_scope):
        pass

    def aplicar_intervenciones(*a, **k):
        raise RuntimeError("pipeline.gasoductos no está instalado")


CLAVE_RESULTADO = "sandbox_resultado"
CLAVE_INFORME = "sandbox_informe_ductos"

# Misma clave que lee `ui/mapa.py`. Si cambia alla, cambia aca.
CLAVE_RED_MAPA = "sandbox_red_gasoductos"

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
        _fragmento_editor(retenidos_rtp, compuestos, params, tbx_en_servicio,
                          factor_mm, comunes)

    with col_salida:
        guardado = st.session_state.get(CLAVE_RESULTADO)
        if guardado is None:
            st.info("Configurá las plantas y dale a **Resolver cascada**.")
            return

        plantas, flujos = guardado
        _bloque_ductos(st.session_state.get(CLAVE_INFORME), factor_mm)
        _bloque_control(flujos, resultados.get("flujos_plantas"), factor_mm)
        _bloque_impacto(flujos, resultados.get("flujos_plantas"), factor_mm)
        _bloque_balance(flujos)
        _bloque_flujos(flujos, factor_mm)
        _bloque_grafo(obtener_registro(), plantas, factor_mm)
        _bloque_kpis(plantas, factor_mm)


def _comunes_con_ductos(comunes, intervenciones, compuestos):
    """Aplica las intervenciones de ductos sobre una COPIA de `comunes`.

    Las tablas de entrada del pipeline oficial no se tocan: si se modificaran en
    el lugar, el resto del tablero pasaria a mostrar los numeros del sandbox sin
    que nadie lo pidiera. Esa es la linea que separa a un sandbox de un cambio.
    """
    activas = [i for i in (intervenciones or []) if i.activa]
    if not activas:
        return comunes, None

    yac, fdi, matriz, informe = aplicar_intervenciones(
        tabla_yacimientos=comunes.get("tabla_total_yacimientos"),
        tabla_flujos_directos=comunes.get("tabla_total_flujos_directos"),
        intervenciones=activas,
        compuestos=compuestos,
        matriz_inyecciones=comunes.get("matriz_inyecciones"),
    )

    efectivo = dict(comunes)
    efectivo["tabla_total_yacimientos"] = yac
    efectivo["tabla_total_flujos_directos"] = fdi
    if matriz is not None:
        efectivo["matriz_inyecciones"] = matriz

    # La red modificada queda a disposicion del tab del mapa, que ofrece un
    # toggle para dibujarla en vez de la oficial. Se deja en `session_state` y
    # no se devuelve porque el mapa no recibe nada de esta funcion: son dos tabs
    # distintos que solo comparten el estado de la sesion.
    _publicar_red_sandbox(yac)

    return efectivo, informe


def _publicar_red_sandbox(yac):
    """Deja la red del sandbox donde el mapa la busca."""
    columnas = {"Area", "Gasoducto", "Volumen_inyectado"}

    if yac is None or not columnas.issubset(yac.columns):
        return

    st.session_state[CLAVE_RED_MAPA] = yac[
        ["Area", "Gasoducto", "Volumen_inyectado"]
    ].rename(columns={"Area": "origen", "Gasoducto": "destino",
                      "Volumen_inyectado": "valor"})


def _cuerpo_editor(retenidos_rtp, compuestos, params, tbx_en_servicio,
                   factor_mm, comunes):
    """Editor + botón de correr. Se envuelve en un fragment (ver abajo)."""

    sub_plantas, sub_ductos = st.tabs(["🏭 Plantas", "🛢️ Gasoductos"])

    with sub_plantas:
        registro, errores, _ = panel_plantas(
            retenidos_rtp=retenidos_rtp,
            compuestos=compuestos,
            config=params,
            tbx_en_servicio=tbx_en_servicio,
            factor_mm=factor_mm,
        )

    with sub_ductos:
        if not GASODUCTOS_DISPONIBLE:
            st.error(
                "El módulo de gasoductos no está instalado: "
                f"`{ERROR_GASODUCTOS}`.\n\n"
                "Falta la carpeta **`pipeline/gasoductos/`** en el repo, con "
                "`__init__.py` e `intervenciones.py`. El resto del tablero "
                "funciona igual."
            )
            intervenciones = []
        else:
            intervenciones = panel_gasoductos(
                tabla_yacimientos=comunes.get("tabla_total_yacimientos"),
                tabla_flujos_directos=comunes.get("tabla_total_flujos_directos"),
                compuestos=compuestos,
                factor_mm=factor_mm,
            )

    st.divider()
    correr = st.button(
        "▶️ Resolver cascada", type="primary", use_container_width=True,
        disabled=bool(errores), key="btn_correr_sandbox")

    if errores:
        st.caption("Corregí los errores de arriba para poder correr.")

    if not correr:
        return

    with st.spinner("Resolviendo…"):
        try:
            comunes_efectivo, informe = _comunes_con_ductos(
                comunes, intervenciones, compuestos)
            plantas, flujos = resolver_cascada(registro, comunes_efectivo)
        except Exception as e:
            st.session_state.pop(CLAVE_RESULTADO, None)
            st.error(f"La cascada falló: {type(e).__name__}: {e}")
            st.exception(e)
            return

    st.session_state[CLAVE_RESULTADO] = (plantas, flujos)
    st.session_state[CLAVE_INFORME] = informe

    # Rerun de APP entero (no del fragment): la salida se dibuja afuera y tiene
    # que enterarse del resultado nuevo. Es el único momento en que se paga el
    # redibujado completo, y pasa una vez por corrida, no por cada checkbox.
    st.rerun()


# `st.fragment` (Streamlit >= 1.37) hace que tocar un widget del editor
# rerunee SOLO este bloque en vez del script entero. Sin esto, cada checkbox
# redibuja los otros siete tabs — tablas, mapa y graphviz incluidos — y por eso
# se siente lento. Con Streamlit viejo se degrada al comportamiento de antes.
def _envolver_en_fragment(funcion):
    """Devuelve `funcion` envuelta en `st.fragment`, si esta version lo tiene.

    Se prueban los dos nombres porque `st.fragment` se llamo
    `st.experimental_fragment` entre 1.33 y 1.36. Y se verifica que lo devuelto
    sea invocable: si no, se degrada al comportamiento de siempre en vez de
    romper el tab.
    """
    for nombre in ("fragment", "experimental_fragment"):
        decorador = getattr(st, nombre, None)
        if decorador is None:
            continue
        try:
            envuelta = decorador(funcion)
        except Exception:
            continue
        if callable(envuelta):
            configurar_scope("fragment")
            configurar_scope_gd("fragment")   # no-op si gasoductos no está
            return envuelta

    # Streamlit viejo: cada widget rerunea el script entero, como antes.
    return funcion


_fragmento_editor = _envolver_en_fragment(_cuerpo_editor)


# ===========================================================================
# Bloques
# ===========================================================================

def _bloque_ductos(informe, factor_mm):
    """Que hicieron las intervenciones de ductos, si hubo alguna."""
    if informe is None:
        return

    for error in informe.errores:
        st.error(error)
    for aviso in informe.avisos:
        st.warning(aviso)

    tabla = informe.tabla()
    if tabla.empty:
        return

    with st.expander(f"🛢️ {len(tabla)} intervención(es) sobre los ductos", expanded=True):
        vista = tabla.copy()
        if "Volumen" in vista.columns:
            vista["Volumen"] = vista["Volumen"] / factor_mm
        st.dataframe(
            vista.style.format({"Volumen": "{:,.2f}"}),
            use_container_width=True, hide_index=True)
        st.caption(
            "Volúmenes en MMm3/d. El total que inyecta cada área no cambia: "
            "sólo se redistribuye entre destinos.")


def _bloque_impacto(flujos_sandbox, flujos_produccion, factor_mm):
    """Cuánto gas ganó o perdió cada planta respecto de la corrida oficial.

    El bloque de control dice SI hay diferencia; este dice DÓNDE. Es la lectura
    que se busca al abrir o cerrar un ducto: el reparto entre áreas es el medio,
    lo que importa es qué planta termina tratando más o menos gas.
    """
    if flujos_produccion is None:
        return

    comunes_idx = [n for n in flujos_sandbox.index if n in flujos_produccion.index]
    nuevas = [n for n in flujos_sandbox.index if n not in flujos_produccion.index]

    if not comunes_idx and not nuevas:
        return

    filas = []

    for nombre in comunes_idx:
        antes = float(flujos_produccion.loc[nombre, "vol_asignado"])
        despues = float(flujos_sandbox.loc[nombre, "vol_asignado"])
        if abs(despues - antes) < 1e-6:
            continue
        filas.append({
            "Planta": nombre,
            "Gas tratado antes": antes / factor_mm,
            "Gas tratado después": despues / factor_mm,
            "Δ": (despues - antes) / factor_mm,
            "LGN Δ": (float(flujos_sandbox.loc[nombre, "lgn_asignado"])
                      - float(flujos_produccion.loc[nombre, "lgn_asignado"])),
        })

    for nombre in nuevas:
        despues = float(flujos_sandbox.loc[nombre, "vol_asignado"])
        filas.append({
            "Planta": f"➕ {nombre}",
            "Gas tratado antes": 0.0,
            "Gas tratado después": despues / factor_mm,
            "Δ": despues / factor_mm,
            "LGN Δ": float(flujos_sandbox.loc[nombre, "lgn_asignado"]),
        })

    if not filas:
        return

    tabla = pd.DataFrame(filas).sort_values("Δ", ascending=False)

    with st.expander("📊 Impacto por planta", expanded=True):
        st.dataframe(
            tabla.style.format({
                "Gas tratado antes": "{:,.2f}", "Gas tratado después": "{:,.2f}",
                "Δ": "{:+,.2f}", "LGN Δ": "{:+,.1f}",
            }),
            use_container_width=True, hide_index=True)
        st.caption(
            "Gas en MMm3/d, LGN en tn/d. Δ es contra la corrida oficial. "
            "Las plantas que no cambiaron no se listan.")


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
