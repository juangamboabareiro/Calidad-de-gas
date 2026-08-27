"""
Panel de gasoductos del sandbox.
================================

Altas y bajas de ductos, que se aplican sobre las tablas de entrada ANTES de
resolver la cascada. Vive en el mismo tab que las plantas porque son la misma
pregunta desde dos lados: las plantas cambian que se hace con el gas, los ductos
cambian por donde llega.

El estado vive en `st.session_state`, igual que el registro de plantas, para
sobrevivir a los reruns.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui.compat import ancho, arrow_safe

# ---------------------------------------------------------------------------
# ESTE modulo es la unica frontera con `pipeline.gasoductos`.
#
# Lo importan `tab_plantas` (para el sub-tab) y `plantas_editor` (para guardar
# las intervenciones en un escenario). Si cada uno se defendiera por su cuenta,
# alcanza con que UNO se olvide para que un archivo faltante tumbe el tablero
# entero — que es exactamente lo que paso.
#
# Entonces la defensa vive aca y en un solo lugar: importar
# `ui.gasoductos_editor` NUNCA falla. Si el paquete no esta, las funciones
# existen igual, devuelven vacio, y `panel_gasoductos` explica que falta.
# ---------------------------------------------------------------------------

try:
    from pipeline.gasoductos.intervenciones import (
        Intervencion,
        aplicar_intervenciones,
        areas_disponibles,
        destinos_area,
        gasoductos_disponibles,
        volumen_area,
    )
    DISPONIBLE = True
    MOTIVO = None
except ImportError as _e:
    DISPONIBLE = False
    MOTIVO = str(_e)

    Intervencion = None

    def aplicar_intervenciones(*a, **k):
        raise RuntimeError(f"pipeline.gasoductos no está disponible: {MOTIVO}")

    def areas_disponibles(*a, **k):
        return []

    def destinos_area(*a, **k):
        import pandas as _pd
        return _pd.Series(dtype="float64")

    def gasoductos_disponibles(*a, **k):
        return []

    def volumen_area(*a, **k):
        return 0.0

from io_.cromatografias_planta import cargar_cromas_extra


CLAVE = "intervenciones_gasoductos"
CLAVE_FLASH = "gasoductos_flash"

_SCOPE = "app"


def configurar_scope(scope: str):
    global _SCOPE
    _SCOPE = scope


def _rerun():
    if _SCOPE == "fragment":
        try:
            st.rerun(scope="fragment")
        except TypeError:
            pass
    st.rerun()


def _flash(tipo, texto):
    st.session_state[CLAVE_FLASH] = (tipo, texto)


def _mostrar_flash():
    mensaje = st.session_state.pop(CLAVE_FLASH, None)
    if mensaje:
        getattr(st, mensaje[0], st.info)(mensaje[1])


def obtener_intervenciones() -> list[Intervencion]:
    return st.session_state.setdefault(CLAVE, [])


# ===========================================================================
# Panel
# ===========================================================================

def panel_gasoductos(tabla_yacimientos, tabla_flujos_directos, compuestos,
                     factor_mm=1000.0):
    """Dibuja el panel y devuelve la lista de intervenciones."""

    intervenciones = obtener_intervenciones()

    st.markdown("### Gasoductos")

    if not DISPONIBLE:
        st.error(
            f"Falta el paquete **`pipeline/gasoductos/`**: `{MOTIVO}`.\n\n"
            "Tienen que existir en el repo los dos archivos:\n"
            "- `pipeline/gasoductos/__init__.py`\n"
            "- `pipeline/gasoductos/intervenciones.py`\n\n"
            "El resto del tablero funciona igual.")
        return intervenciones
    st.caption(
        "El volumen que inyecta cada **área** no cambia: un ducto no crea ni "
        "destruye gas, sólo cambia por dónde sale. Toda intervención es una "
        "redistribución dentro del área.")

    _mostrar_flash()

    if tabla_yacimientos is None or tabla_yacimientos.empty:
        st.info("No hay tabla de yacimientos para intervenir.")
        return intervenciones

    _bloque_alta(tabla_yacimientos, tabla_flujos_directos, compuestos,
                 intervenciones, factor_mm)
    _bloque_baja(tabla_yacimientos, tabla_flujos_directos, intervenciones, factor_mm)
    _bloque_activas(intervenciones, factor_mm)

    return intervenciones


# ---------------------------------------------------------------------------

def _bloque_alta(yac, fdi, compuestos, intervenciones, factor_mm):
    with st.expander("Abrir un gasoducto", expanded=False):
        areas = areas_disponibles(yac)
        if not areas:
            st.info("No hay áreas con inyección.")
            return

        # Ordenadas por volumen: en un desplegable de ~130, las que importan
        # tienen que estar arriba.
        area = st.selectbox(
            "Área de origen", areas, key="gd_area",
            help="Ordenadas por volumen inyectado, de mayor a menor.")

        total = volumen_area(yac, area)
        reparto = destinos_area(yac, area)

        plantas = _plantas_destino(fdi)
        if not plantas:
            st.warning(
                "No se pudieron deducir las plantas destino de la tabla de "
                "flujos directos.")
            return

        col_a, col_b = st.columns(2)
        nombre = col_a.text_input("Nombre del gasoducto nuevo", key="gd_nombre")
        planta = col_b.selectbox("Planta destino", plantas, key="gd_planta")

        # El tope es el total del área: un ducto no puede llevar más gas del que
        # el área produce. El slider lo hace evidente sin tener que validarlo.
        volumen_mm = st.slider(
            f"Volumen por el ducto nuevo [MMm3/d] — {area} inyecta "
            f"{total / factor_mm:,.2f}",
            min_value=0.0, max_value=float(total / factor_mm),
            value=float(total / factor_mm) * 0.25, step=0.01, key="gd_vol",
            help="El tope es el total que inyecta el área. El resto se reparte "
                 "entre los destinos actuales manteniendo su proporción.")

        _previsualizar(area, reparto, total, volumen_mm * factor_mm,
                       nombre or "(ducto nuevo)", factor_mm)

        archivo = st.file_uploader(
            "Cromatografía del gasoducto (.xlsx/.csv)", type=["xlsx", "xlsm", "csv"],
            key="gd_croma",
            help="Mismo formato que las cromas de planta. Si no cargás ninguna, "
                 "se hereda la del área, que es la suposición razonable: el gas "
                 "del ducto nuevo es el mismo gas del área.")

        cromato, avisos_croma = _leer_cromato(archivo, compuestos, factor_mm)
        for aviso in avisos_croma:
            st.warning(aviso)
        if cromato is not None:
            st.caption("Cromatografía cargada: "
                       + " · ".join(f"{c} {cromato[c]:.3f}"
                                    for c in list(cromato.index)[:6]) + " …")

        if st.button("Abrir gasoducto", type="primary",
                     **ancho(), key="gd_btn_alta"):
            nombre = (nombre or "").strip()
            if not nombre:
                st.error("Poné un nombre para el gasoducto.")
            elif any(i.nombre == nombre for i in intervenciones):
                st.error(f"Ya hay una intervención sobre '{nombre}'.")
            else:
                intervenciones.append(Intervencion(
                    tipo="alta", nombre=nombre, area_origen=area,
                    planta_destino=planta, volumen=volumen_mm * factor_mm,
                    cromato=cromato))
                _flash("success",
                       f"**{nombre}** abierto: {area} → {planta}, "
                       f"{volumen_mm:,.2f} MMm3/d. Dale a **Resolver cascada**.")
                _rerun()


def _previsualizar(area, reparto, total, volumen, nombre, factor_mm):
    """Tabla antes/después del reparto del área.

    Es el corazón de la funcionalidad y por eso se muestra ANTES de aplicar: la
    regla "los demás se ajustan proporcionalmente" es fácil de enunciar y difícil
    de verificar de cabeza con seis destinos. Acá se ve.

    Nota sobre "proporcional a como estaban en la matriz de inyección": los
    volúmenes YA son `Volumen * Coef_Inyeccion`, así que repartir en proporción
    a los volúmenes actuales es exactamente lo mismo que repartir en proporción
    a los coeficientes de la matriz. No hay que ir a buscarlos.
    """
    if total <= 0:
        st.warning(f"'{area}' no inyecta nada: no hay volumen para repartir.")
        return

    factor = 1 - (volumen / total)

    filas = []
    for destino, actual in reparto.items():
        despues = actual * factor
        filas.append({
            "Destino": destino,
            "Ahora": actual / factor_mm,
            "Después": despues / factor_mm,
            "Δ": (despues - actual) / factor_mm,
            "% del área": despues / total * 100,
        })

    filas.append({
        "Destino": f"{nombre} (nuevo)",
        "Ahora": 0.0,
        "Después": volumen / factor_mm,
        "Δ": volumen / factor_mm,
        "% del área": volumen / total * 100,
    })

    tabla = pd.DataFrame(filas)

    st.caption(
        f"Los destinos actuales pasan a **{factor:.1%}** de su volumen. "
        f"El área sigue inyectando **{total / factor_mm:,.2f} MMm3/d**.")

    st.dataframe(
        arrow_safe(tabla).style.format({
            "Ahora": "{:,.2f}", "Después": "{:,.2f}",
            "Δ": "{:+,.2f}", "% del área": "{:,.1f}%",
        }),
        **ancho(), hide_index=True)

    # El total tiene que cerrar. Si no cierra hay un bug, y es mejor verlo acá
    # que descubrirlo comparando plantas tres pasos después.
    suma = float(tabla["Después"].sum() * factor_mm)
    if abs(suma - total) > 1e-6:
        st.error(
            f"El reparto no cierra: suma {suma / factor_mm:,.4f} contra "
            f"{total / factor_mm:,.4f} del área.")


def _bloque_baja(yac, fdi, intervenciones, factor_mm):
    with st.expander("Sacar un gasoducto por mantenimiento", expanded=False):
        ductos = gasoductos_disponibles(yac, fdi)
        ya = {i.nombre for i in intervenciones}
        ductos = [d for d in ductos if d not in ya]

        if not ductos:
            st.info("No quedan gasoductos disponibles para dar de baja.")
            return

        st.caption(
            "Su volumen se reparte entre los otros destinos de cada área que le "
            "inyectaba, proporcional a como estaban. **Los ductos todavía no "
            "tienen capacidad máxima**, así que el gas siempre entra: la baja "
            "mueve gas de lado, no genera bypass.")

        col_a, col_b = st.columns([2, 1])
        ducto = col_a.selectbox("Gasoducto", ductos, key="gd_baja_sel")

        entra = yac[yac["Gasoducto"].astype(str) == ducto]
        por_area = entra.groupby("Area")["Volumen_inyectado"].sum().sort_values(ascending=False)

        st.caption(
            f"**{ducto}** transporta **{por_area.sum() / factor_mm:,.2f} MMm3/d** "
            f"de {len(por_area)} área(s).")

        # Las áreas cuyo ÚNICO destino es este ducto son el caso interesante:
        # su gas no tiene a dónde ir y el modelo no puede inventarle una ruta.
        sin_salida = []
        for area in por_area.index:
            otros = yac[(yac["Area"] == area) & (yac["Gasoducto"].astype(str) != ducto)]
            if float(otros["Volumen_inyectado"].sum()) <= 1e-9:
                sin_salida.append((area, float(por_area[area])))

        if sin_salida:
            st.warning(
                f"{len(sin_salida)} área(s) sólo inyectan a este ducto. Ese "
                "gas **no se redistribuye** (no hay a dónde) y el total "
                "inyectado baja: "
                + ", ".join(f"{a} ({v / factor_mm:,.2f})" for a, v in sin_salida[:5])
                + (" …" if len(sin_salida) > 5 else ""))

        if col_b.button("Sacar de servicio", **ancho(),
                        key="gd_btn_baja"):
            intervenciones.append(Intervencion(tipo="baja", nombre=ducto))
            _flash("success",
                   f"**{ducto}** fuera de servicio. Dale a **Resolver cascada**.")
            _rerun()


def _bloque_activas(intervenciones, factor_mm):
    if not intervenciones:
        st.caption("Sin intervenciones: los ductos quedan como en la corrida oficial.")
        return

    st.caption(f"**{len(intervenciones)} intervención(es)**")

    for i, interv in enumerate(list(intervenciones)):
        col_a, col_b, col_c = st.columns([4, 1, 1])

        if interv.tipo == "alta":
            texto = (f"**{interv.nombre}** · {interv.area_origen} → "
                     f"{interv.planta_destino} · {interv.volumen / factor_mm:,.2f} MMm3/d"
                     + (" · croma propia" if interv.cromato is not None else ""))
        else:
            texto = f"**{interv.nombre}** fuera de servicio"

        col_a.markdown(texto if interv.activa else f"~~{texto}~~")

        # Desactivar en vez de borrar: deja comparar con y sin la intervención
        # sin tener que volver a cargarla.
        interv.activa = col_b.toggle(
            "on", value=interv.activa, key=f"gd_act_{i}", label_visibility="collapsed")

        if col_c.button("Eliminar", key=f"gd_del_{i}", help="Quitar esta intervención"):
            intervenciones.pop(i)
            _rerun()


# ===========================================================================
# Helpers
# ===========================================================================

def _plantas_destino(fdi) -> list[str]:
    """Destinos de la tabla de flujos directos: las plantas que reciben ducto."""
    if fdi is None or fdi.empty or "Gasoducto" not in fdi.columns:
        return []
    return sorted(set(fdi["Gasoducto"].dropna().astype(str)))


def _leer_cromato(archivo, compuestos, factor_mm):
    """Del archivo subido saca UNA composición.

    Si trae varias corrientes se promedian ponderando por volumen: al ducto
    entra una sola mezcla, no varias en paralelo.
    """
    if archivo is None:
        return None, []

    cromas, avisos = cargar_cromas_extra(archivo, compuestos, factor_volumen=factor_mm)
    if not cromas:
        return None, avisos or ["No se pudo leer ninguna cromatografía del archivo."]

    entradas = [e for lista in cromas.values() for e in lista]
    total = sum(e["vol_derivacion"] for e in entradas)

    if total <= 0:
        return entradas[0]["cromato_derivacion"], avisos

    mezcla = sum(
        (e["cromato_derivacion"] * e["vol_derivacion"] for e in entradas),
        pd.Series(0.0, index=list(compuestos), dtype="float64"),
    ) / total

    if len(entradas) > 1:
        avisos.append(
            f"El archivo trae {len(entradas)} corrientes: se promediaron "
            "ponderando por volumen, porque al ducto entra una sola mezcla.")

    return mezcla, avisos
