"""
Interfaz Streamlit — Balance de Gas
===================================

Corre el mismo pipeline que main.py con los parametros que se elijan en la
barra lateral, y muestra:

  - Resumen          : KPIs y balance por planta (entrante = procesado + derivado + bypass)
  - Tablas           : explorador de TODAS las tablas + comparador contra el Excel de referencia
  - Cadena de gas    : gasoductos -> plantas y derivaciones entre plantas
  - Una tab por planta con el esquema de bloques

NOTA SOBRE PARAMETROS EN VIVO
-----------------------------
Varios modulos del pipeline (domain/ctes_gas.py, pipeline/preprocesamiento.py,
pipeline/plantas/*.py) leen config a nivel de modulo. El valor queda congelado
en el primer import, entonces cambiar el archivo subido o una capacidad en la
sidebar, por si solo, NO impactaria el resultado. Mientras eso no se
refactorice, se recargan en caliente (importlib.reload) los modulos afectados,
en orden de dependencias, en cada ejecucion. Ver _recargar_modulos().
"""

import base64
import importlib
import io
import re
import tempfile
import textwrap
import unicodedata
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

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


# ===========================================================================
# 1. Recarga en caliente de los modulos que leen config a nivel de modulo
# ===========================================================================

def _recargar_modulos(params: dict) -> dict:
    """Escribe los parametros en config y recarga los modulos que lo leen.

    El orden importa: ctes_gas primero (recalcula constantes con el PATH nuevo),
    despues los que importan de ctes_gas.
    """
    for clave, valor in params.items():
        setattr(config, clave, valor)

    import domain.ctes_gas as ctes_gas
    importlib.reload(ctes_gas)

    import domain.propiedades_gas as propiedades_gas
    importlib.reload(propiedades_gas)

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
        "ctes": ctes_gas,
        "propiedades_gas": propiedades_gas,
        "preprocesamiento": preprocesamiento,
        "flujo_plantas": flujo_plantas,
        "TTY": TTY,
        "MEGA": MEGA,
    }


# ===========================================================================
# 2. Helpers de presentacion
# ===========================================================================

def _fmt(valor, decimales=1, unidad=""):
    """Numero formateado, o '—' si todavia no hay dato."""
    if valor is None:
        return "—"
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if pd.isna(f):
        return "—"
    return f"{f:,.{decimales}f}{unidad}"


def _num(valor, default=0.0):
    """Escalar float a partir de float / Series / DataFrame de un solo valor."""
    if valor is None:
        return default
    if isinstance(valor, (pd.Series, pd.DataFrame)):
        vals = pd.to_numeric(pd.Series(valor.values.ravel()), errors="coerce").dropna()
        return float(vals.sum()) if len(vals) else default
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(f) else f


def _a_dataframe(obj, nombre_valor="Valor", nombre_indice="Compuesto"):
    """Series / DataFrame / escalar -> DataFrame presentable, sin asumir la
    forma exacta que devuelve cada funcion de dominio."""
    if obj is None:
        return pd.DataFrame()
    if isinstance(obj, pd.DataFrame):
        df = obj.copy()
        if not isinstance(df.index, pd.RangeIndex):
            df = df.reset_index()
        return df
    if isinstance(obj, pd.Series):
        df = obj.to_frame(name=nombre_valor)
        df.index.name = df.index.name or nombre_indice
        return df.reset_index()
    try:
        return pd.DataFrame(obj)
    except Exception:
        return pd.DataFrame({nombre_valor: [obj]})


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(texto).lower()).strip("_")


def _norm_col(texto) -> str:
    """Normaliza un nombre de columna para poder aparearlo con el del Excel:
    sin tildes, sin espacios ni guiones, minusculas."""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _descargar_csv(df: pd.DataFrame, nombre: str, key: str, label=None):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button(
        label or f"⬇️ Descargar {nombre}.csv",
        data=buf.getvalue(),
        file_name=f"{nombre}.csv",
        mime="text/csv",
        key=key,
    )


# ===========================================================================
# 3. Esquema de bloques de la planta (SVG)
# ===========================================================================
# Por que antes se veia roto:
#   1. st.markdown(svg, unsafe_allow_html=True) sanitiza el HTML y ademas el
#      string empezaba con salto de linea + 4 espacios de indentacion, asi que
#      Markdown lo tomaba como bloque de codigo y lo mostraba como texto.
#   2. Los tres esquemas usaban el mismo id="arrow" para el marker: en una
#      misma pagina los ids colisionan y las flechas se pierden.
#   3. <defs> estaba al final, despues de los elementos que lo referencian.
# Ahora: dedent + strip, ids unicos por planta, defs al inicio, y render con
# components.html (iframe), que no pasa por el sanitizador de Markdown.

_PALETA = {
    "tinta": "#10242E",
    "texto": "#2E4552",
    "suave": "#7D919C",
    "linea": "#10242E",
    "procesado": "#1F7A6B",
    "derivado": "#E0A93B",
    "bypass": "#C25B4A",
    "fondo_box": "#F2F5F6",
}


def _svg_esquema_planta(
    nombre_planta: str,
    flujos: dict,
    capacidad_ingreso=None,
    capacidad_evacuacion=None,
    lgn_cortes=None,
    unidad_gas="MMm3/d",
    unidad_lgn="unid. retenidos",
    color_planta="#1F7A6B",
    destino_derivacion=None,
) -> str:
    """Diagrama de bloques de una planta.

    El bloque central es una barra proporcional que descompone el volumen
    entrante en procesado / derivado / bypass: la identidad del balance
    (entrante = procesado + derivado + bypass) queda visible, no solo escrita.

    flujos : dict — salida de calcular_flujos_planta.
    lgn_cortes : dict {'etano': x, 'propano': y, ...} — retenidos por corte.
    """
    flujos = flujos or {}
    entrante = _num(flujos.get("vol_entrante"))
    procesado = _num(flujos.get("vol_procesado"))
    derivado = _num(flujos.get("vol_derivado"))
    bypass = _num(flujos.get("bypass"))
    lgn = _num(flujos.get("lgn_potencial"))
    fraccion = flujos.get("fraccion_tratable")

    uid = _slug(nombre_planta)
    cortes = lgn_cortes or {}

    # Barra proporcional: si no hay volumen, todo el ancho va a "procesado".
    ancho_barra = 300.0
    total = entrante if entrante > 0 else 1.0
    w_proc = max(ancho_barra * procesado / total, 0.0)
    w_deriv = max(ancho_barra * derivado / total, 0.0)
    w_byp = max(ancho_barra * bypass / total, 0.0)
    x_proc = 230.0
    x_deriv = x_proc + w_proc
    x_byp = x_deriv + w_deriv

    ocupacion = (entrante / capacidad_ingreso * 100) if _num(capacidad_ingreso) else None

    filas_lgn = ""
    y = 74
    for etiqueta in ("etano", "propano", "butanos", "gasolina"):
        if etiqueta in cortes:
            filas_lgn += (
                f'<text x="42" y="{y}" font-size="12" fill="{_PALETA["texto"]}">{etiqueta.capitalize()}</text>'
                f'<text x="175" y="{y}" font-size="12" text-anchor="end" '
                f'fill="{_PALETA["tinta"]}">{_fmt(cortes[etiqueta])}</text>'
            )
            y += 18

    etiqueta_derivado = (
        f"Derivado → {destino_derivacion}" if destino_derivacion else "Derivado"
    )

    svg = f"""
<svg viewBox="0 0 780 430" xmlns="http://www.w3.org/2000/svg"
     font-family="'IBM Plex Sans','Segoe UI',Arial,sans-serif"
     style="width:100%;height:auto;">
  <defs>
    <marker id="flecha_{uid}" markerWidth="9" markerHeight="9" refX="7" refY="4.5"
            orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L9,4.5 L0,9 Z" fill="{_PALETA["linea"]}"/>
    </marker>
    <marker id="flecha_fina_{uid}" markerWidth="8" markerHeight="8" refX="6" refY="4"
            orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L8,4 L0,8 Z" fill="{_PALETA["bypass"]}"/>
    </marker>
  </defs>

  <!-- ===== LGN retenido ===== -->
  <rect x="24" y="24" width="170" height="{40 + 18 * max(len(cortes), 1)}"
        fill="{_PALETA["fondo_box"]}" stroke="{_PALETA["suave"]}" stroke-width="1" rx="3"/>
  <text x="42" y="46" font-size="11" letter-spacing="1.2"
        fill="{_PALETA["suave"]}">LGN RETENIDO</text>
  <text x="175" y="46" font-size="11" text-anchor="end"
        fill="{_PALETA["suave"]}">{unidad_lgn}</text>
  {filas_lgn}

  <text x="24" y="{84 + 18 * max(len(cortes), 1)}" font-size="12.5" font-weight="600"
        fill="{_PALETA["tinta"]}">Total {_fmt(lgn, 1)}</text>
  <text x="24" y="{102 + 18 * max(len(cortes), 1)}" font-size="11.5"
        fill="{_PALETA["texto"]}">Evacuación {_fmt(capacidad_evacuacion, 1)}</text>

  <!-- flecha LGN: sale de la planta hacia arriba -->
  <line x1="380" y1="200" x2="380" y2="140" stroke="{_PALETA["linea"]}" stroke-width="1.6"
        marker-end="url(#flecha_{uid})"/>
  <text x="392" y="150" font-size="11.5" fill="{_PALETA["texto"]}">
    a LGN ({_fmt(lgn, 1)} {unidad_lgn})
  </text>

  <!-- ===== Gas IN ===== -->
  <line x1="24" y1="240" x2="222" y2="240" stroke="{_PALETA["linea"]}" stroke-width="3.5"
        marker-end="url(#flecha_{uid})"/>
  <text x="24" y="226" font-size="11" letter-spacing="1.2"
        fill="{_PALETA["suave"]}">GAS RICO IN</text>
  <text x="24" y="264" font-size="15" font-weight="700"
        fill="{_PALETA["tinta"]}">{_fmt(entrante, 2)} {unidad_gas}</text>

  <!-- ===== Caja de planta con barra proporcional ===== -->
  <rect x="228" y="198" width="304" height="84" fill="none"
        stroke="{_PALETA["linea"]}" stroke-width="1.6" rx="3"/>
  <text x="380" y="192" font-size="14" font-weight="700" text-anchor="middle"
        fill="{color_planta}">{nombre_planta.upper()}</text>

  <rect x="{x_proc:.1f}" y="240" width="{w_proc:.1f}" height="40"
        fill="{_PALETA["procesado"]}" opacity="0.9"/>
  <rect x="{x_deriv:.1f}" y="240" width="{w_deriv:.1f}" height="40"
        fill="{_PALETA["derivado"]}" opacity="0.9"/>
  <rect x="{x_byp:.1f}" y="240" width="{w_byp:.1f}" height="40"
        fill="{_PALETA["bypass"]}" opacity="0.9"/>
  <text x="380" y="228" font-size="11" text-anchor="middle" fill="{_PALETA["suave"]}">
    fracción tratable {_fmt((fraccion or 0) * 100, 1, "%")}
  </text>

  <!-- ===== Gas tratado OUT ===== -->
  <line x1="538" y1="240" x2="742" y2="240" stroke="{_PALETA["linea"]}" stroke-width="3.5"
        marker-end="url(#flecha_{uid})"/>
  <text x="548" y="226" font-size="11" letter-spacing="1.2"
        fill="{_PALETA["suave"]}">GAS RESIDUAL OUT</text>
  <text x="548" y="264" font-size="15" font-weight="700"
        fill="{_PALETA["procesado"]}">{_fmt(procesado, 2)} {unidad_gas}</text>

  <!-- ===== Derivado ===== -->
  <line x1="500" y1="284" x2="500" y2="330" stroke="{_PALETA["derivado"]}" stroke-width="2.5"/>
  <line x1="500" y1="330" x2="742" y2="330" stroke="{_PALETA["derivado"]}" stroke-width="2.5"
        marker-end="url(#flecha_{uid})"/>
  <text x="512" y="322" font-size="11.5" fill="{_PALETA["texto"]}">{etiqueta_derivado}</text>
  <text x="512" y="348" font-size="13.5" font-weight="700"
        fill="{_PALETA["derivado"]}">{_fmt(derivado, 2)} {unidad_gas}</text>

  <!-- ===== Bypass ===== -->
  <line x1="300" y1="284" x2="300" y2="380" stroke="{_PALETA["bypass"]}" stroke-width="2"
        stroke-dasharray="5,4"/>
  <line x1="300" y1="380" x2="742" y2="380" stroke="{_PALETA["bypass"]}" stroke-width="2"
        stroke-dasharray="5,4" marker-end="url(#flecha_fina_{uid})"/>
  <text x="312" y="372" font-size="11.5" fill="{_PALETA["texto"]}">Bypass (sin tratar)</text>
  <text x="312" y="398" font-size="13.5" font-weight="700"
        fill="{_PALETA["bypass"]}">{_fmt(bypass, 2)} {unidad_gas}</text>

  <!-- ===== Ocupacion de ingreso ===== -->
  <text x="24" y="372" font-size="11" letter-spacing="1.2"
        fill="{_PALETA["suave"]}">INGRESO</text>
  <text x="24" y="398" font-size="12.5" fill="{_PALETA["texto"]}">
    {_fmt(entrante, 1)} / {_fmt(capacidad_ingreso, 1)} {unidad_gas}
    · {_fmt(ocupacion, 0, "%") if ocupacion is not None else "—"}
  </text>
</svg>
"""
    return textwrap.dedent(svg).strip()


def _mostrar_svg(svg: str, alto: int = 440, key: str = ""):
    """Render de SVG que no pasa por el sanitizador de Markdown."""
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    html = (
        '<div style="width:100%;background:#FFFFFF;border:1px solid #E3E8EA;'
        'border-radius:6px;padding:10px 12px;box-sizing:border-box;">'
        f'<img style="width:100%;height:auto;" src="data:image/svg+xml;base64,{b64}"/>'
        "</div>"
    )
    components.html(html, height=alto, scrolling=False)
    return svg


# ===========================================================================
# 4. Grafos (graphviz): red de gasoductos y cadena de plantas
# ===========================================================================

def _dot_red_gasoductos(edges: pd.DataFrame, top_n: int = 25) -> str:
    """Area -> Gasoducto, con el volumen como etiqueta del arco."""
    lineas = [
        "digraph G {",
        '  rankdir="LR"; bgcolor="transparent";',
        '  node [fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=9, color="#7D919C"];',
    ]
    if edges is not None and not edges.empty:
        e = edges.dropna(subset=["origen", "destino"]).copy()
        e["valor"] = pd.to_numeric(e.get("valor"), errors="coerce").fillna(0.0)
        e = e.groupby(["origen", "destino"], as_index=False)["valor"].sum()
        e = e.sort_values("valor", ascending=False).head(top_n)
        for origen in e["origen"].unique():
            lineas.append(
                f'  "{origen}" [shape=box, style="rounded,filled", '
                f'fillcolor="#F2F5F6", color="#7D919C"];'
            )
        for destino in e["destino"].unique():
            lineas.append(
                f'  "{destino}" [shape=box, style=filled, fillcolor="#1F7A6B", '
                f'fontcolor="white", color="#10242E"];'
            )
        for _, fila in e.iterrows():
            lineas.append(
                f'  "{fila["origen"]}" -> "{fila["destino"]}" '
                f'[label=" {_fmt(fila["valor"], 1)}"];'
            )
    lineas.append("}")
    return "\n".join(lineas)


def _dot_cadena_plantas(plantas: dict) -> str:
    """TTY-TBX -> TTY-DP -> MEGA, con procesado / derivado / bypass por planta."""
    lineas = [
        "digraph G {",
        '  rankdir="LR"; bgcolor="transparent"; nodesep=0.6;',
        '  node [shape=plaintext, fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]
    orden = [n for n in plantas.keys()]
    for nombre in orden:
        f = plantas[nombre].get("flujos", {}) or {}
        lineas.append(
            f'  "{nombre}" [label=<'
            f'<table border="0" cellborder="1" cellspacing="0" cellpadding="4">'
            f'<tr><td bgcolor="#10242E"><font color="white"><b>{nombre}</b></font></td></tr>'
            f'<tr><td align="left">entra {_fmt(_num(f.get("vol_entrante")), 2)}</td></tr>'
            f'<tr><td align="left">trata {_fmt(_num(f.get("vol_procesado")), 2)}</td></tr>'
            f'<tr><td align="left">LGN {_fmt(_num(f.get("lgn_potencial")), 1)}</td></tr>'
            f"</table>>];"
        )
        byp = _num(f.get("bypass"))
        if byp > 0:
            lineas.append(f'  "bypass_{_slug(nombre)}" [label="bypass\\n{_fmt(byp, 2)}", fontcolor="#C25B4A"];')
            lineas.append(
                f'  "{nombre}" -> "bypass_{_slug(nombre)}" '
                f'[color="#C25B4A", style=dashed];'
            )
    for i in range(len(orden) - 1):
        f = plantas[orden[i]].get("flujos", {}) or {}
        deriv = _num(f.get("vol_derivado"))
        lineas.append(
            f'  "{orden[i]}" -> "{orden[i + 1]}" '
            f'[label=" deriva {_fmt(deriv, 2)}", color="#E0A93B", penwidth=2];'
        )
    lineas.append("}")
    return "\n".join(lineas)


# ===========================================================================
# 5. Pipeline
# ===========================================================================

def ejecutar_pipeline(path: str, params: dict, guardar_csvs: bool) -> dict:
    mods = _recargar_modulos({**params, "PATH_INPUTS": path})
    ctes = mods["ctes"]
    COMPUESTOS = ctes.COMPUESTOS
    preprocesar_inputs = mods["preprocesamiento"].preprocesar_inputs
    modelar_TTY = mods["TTY"].modelar_TTY
    modelar_MEGA = mods["MEGA"].modelar_MEGA
    calcular_DERIVACION = mods["flujo_plantas"].calcular_DERIVACION

    with st.status("Cargando inputs...", expanded=False) as status:
        inyeccion_9300 = load_inyeccion_9300(path)
        coeficientes = load_coeficientes(path)
        retenidos_RTP = load_retenidos_rtp(path)
        flujos_directos = load_flujos_directos(path)
        yacimientos = load_yacimientos(path)
        detalles_hubs = load_detalles_hubs(path)
        propiedades = load_propiedades(path)
        plantas_yacimientos = load_plantas_yacimientos(path)
        status.update(label="Inputs cargados ✅", state="complete")

    with st.status("Preprocesando...", expanded=False) as status:
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

    with st.status("Inyección y cruces por área...", expanded=False) as status:
        inyeccion_std = calcular_inyeccion_std(inyeccion_9300, coeficientes)
        inyeccion = calcular_inyeccion(inyeccion_std, plantas_yacimientos)
        inyeccion_area = calcular_inyeccion_area(inyeccion, matriz_inyecciones)

        inyeccion_yacimientos_areas = calcular_inyeccion_yacimientos_areas(
            yacimientos, plantas_yacimientos, inyeccion_area
        )
        inyeccion_detalles_hubs = calcular_inyeccion_detalles_hubs(
            detalles_hubs, plantas_yacimientos
        )
        inyeccion_flujos_directos = calcular_inyeccion_flujos_directos(
            flujos_directos, matriz_inyecciones
        )
        status.update(label="Cruces listos ✅", state="complete")

    with st.status("Tablas totales...", expanded=False) as status:
        periodo = params["PERIODO_CONSIDERADO"]
        tabla_total_yacimientos = calcular_tabla_total_yacimientos(
            inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area,
            premisas_areas, periodo, COMPUESTOS,
        )
        tabla_total_flujos_directos = calcular_tabla_total_flujos_directos(
            inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas,
            periodo, COMPUESTOS,
        )
        tabla_total_detalles_hubs = calcular_tabla_total_detalles_hubs(
            inyeccion_detalles_hubs, premisas_areas
        )

        ctes_gas_args = (
            propiedades, COMPUESTOS, ctes.PRESION_BASE, ctes.TEMPERATURA_BASE,
            ctes.CONSTANTE_GAS, ctes.DENSIDAD_AIRE, ctes.CONVERSION,
        )
        tabla_total_yacimientos = calcular_propiedades_gas(tabla_total_yacimientos, *ctes_gas_args)
        tabla_total_flujos_directos = calcular_propiedades_gas(tabla_total_flujos_directos, *ctes_gas_args)
        tabla_total_detalles_hubs = calcular_propiedades_gas(tabla_total_detalles_hubs, *ctes_gas_args)
        status.update(label="Tablas totales listas ✅", state="complete")

    if guardar_csvs:
        guardar(tabla_total_yacimientos, "TBL_TTL_YCS", activar=True)
        guardar(tabla_total_flujos_directos, "TBL_TTL_DTOS", activar=True)
        guardar(tabla_total_detalles_hubs, "TBL_TTL_DH", activar=True)

    # --- Plantas: cadena TTY-TBX -> TTY-DP -> MEGA ---------------------------
    with st.status("Modelando plantas y derivaciones...", expanded=False) as status:
        retenidos_TTY_DP = retenidos_RTP[COMPUESTOS][retenidos_RTP["Planta"] == "Dew point"]
        retenidos_TTY_TBX = retenidos_RTP[COMPUESTOS][retenidos_RTP["Planta"] == "TBX"]
        retenidos_MEGA = retenidos_RTP[COMPUESTOS][retenidos_RTP["Planta"] == "TBX MEGA"]

        comunes = dict(
            matriz_inyecciones=load_matriz_inyecciones(path),
            calcular_retenidos=calcular_retenidos,
            tabla_total_flujos_directos=tabla_total_flujos_directos,
            propiedades=propiedades,
            COMPUESTOS=COMPUESTOS,
        )

        TTY_TBX = modelar_TTY(
            **comunes,
            retenidos_TTY=retenidos_TTY_TBX,
            CAPACIDAD_TTY=params["CAPACIDAD_TTY_TBX"],
            CAPACIDAD_EVACUACION_TTY=params["CAPACIDAD_EVACUACION_TTY_TBX"],
            MAX_DERIVACION_PLANTA_A_PLANTA=params["MAX_DERIVACION_TTY_TBX_A_TTY_DP"],
            factor_retenidos=params["FACTOR_RETENIDOS_TTY_TBX"],
        )

        derivacion_TBX_a_DP = calcular_DERIVACION(
            flujos_origen=TTY_TBX["flujos"],
            gas_rico_IN_origen=TTY_TBX["gas_rico_IN"],
            nombre_origen="tty_tbx",
        )

        TTY_DP = modelar_TTY(
            **comunes,
            retenidos_TTY=retenidos_TTY_DP,
            CAPACIDAD_TTY=params["CAPACIDAD_TTY_DP"],
            CAPACIDAD_EVACUACION_TTY=params["CAPACIDAD_EVACUACION_TTY_DP"],
            derivaciones=[derivacion_TBX_a_DP],
            MAX_DERIVACION_PLANTA_A_PLANTA=params["MAX_DERIVACION_TTY_DP_A_MEGA"],
            factor_retenidos=params["FACTOR_RETENIDOS_TTY_DP"],
        )

        derivacion_DP_a_MEGA = calcular_DERIVACION(
            flujos_origen=TTY_DP["flujos"],
            gas_rico_IN_origen=TTY_DP["gas_rico_IN"],
            nombre_origen="tty_dp",
        )

        MEGA = modelar_MEGA(
            **comunes,
            retenidos_MEGA=retenidos_MEGA,
            CAPACIDAD_MEGA=params["CAPACIDAD_MEGA"],
            CAPACIDAD_EVACUACION_MEGA=params["CAPACIDAD_EVACUACION_MEGA"],
            derivaciones=[derivacion_DP_a_MEGA],
            factor_retenidos=params["FACTOR_RETENIDOS_MEGA"],
        )
        status.update(label="Plantas modeladas ✅", state="complete")

    columnas_flujos = [
        "vol_entrante", "vol_procesado", "vol_derivado", "bypass",
        "excedente", "lgn_potencial", "fraccion_tratable",
    ]
    flujos_plantas = pd.DataFrame({
        "TTY_TBX": TTY_TBX["flujos"],
        "TTY_DP": TTY_DP["flujos"],
        "MEGA": MEGA["flujos"],
    }).T.reindex(columns=columnas_flujos)

    # Chequeo de balance: se muestra, no se hace assert (no queremos matar la app)
    desvio_balance = float(
        (flujos_plantas["vol_entrante"]
         - flujos_plantas[["vol_procesado", "vol_derivado", "bypass"]].sum(axis=1))
        .abs().max()
    )

    red_gasoductos = pd.DataFrame(columns=["origen", "destino", "valor"])
    if {"Area", "Gasoducto", "Volumen_inyectado"}.issubset(tabla_total_yacimientos.columns):
        red_gasoductos = (
            tabla_total_yacimientos[["Area", "Gasoducto", "Volumen_inyectado"]]
            .rename(columns={"Area": "origen", "Gasoducto": "destino", "Volumen_inyectado": "valor"})
        )

    plantas = {
        "TTY - TBX": {
            **TTY_TBX,
            "capacidad_ingreso": params["CAPACIDAD_TTY_TBX"],
            "capacidad_evacuacion": params["CAPACIDAD_EVACUACION_TTY_TBX"],
            "factor_retenidos": params["FACTOR_RETENIDOS_TTY_TBX"],
            "destino_derivacion": "TTY - DP",
            "color": "#1F7A6B",
        },
        "TTY - Dew Point": {
            **TTY_DP,
            "capacidad_ingreso": params["CAPACIDAD_TTY_DP"],
            "capacidad_evacuacion": params["CAPACIDAD_EVACUACION_TTY_DP"],
            "factor_retenidos": params["FACTOR_RETENIDOS_TTY_DP"],
            "destino_derivacion": "MEGA",
            "color": "#2A6F97",
            "derivacion_entrante": derivacion_TBX_a_DP,
        },
        "MEGA": {
            **MEGA,
            "capacidad_ingreso": params["CAPACIDAD_MEGA"],
            "capacidad_evacuacion": params["CAPACIDAD_EVACUACION_MEGA"],
            "factor_retenidos": params["FACTOR_RETENIDOS_MEGA"],
            "destino_derivacion": None,
            "color": "#5B4B8A",
            "derivacion_entrante": derivacion_DP_a_MEGA,
        },
    }

    return {
        "params": params,
        "inputs": {
            "inyeccion_9300": inyeccion_9300,
            "coeficientes": coeficientes,
            "retenidos_RTP": retenidos_RTP,
            "propiedades": propiedades,
            "premisas_areas": premisas_areas,
            "matriz_inyecciones": matriz_inyecciones,
            "coefs_inyeccion_area": coefs_inyeccion_area,
            "plantas_yacimientos": plantas_yacimientos,
            "flujos_directos": flujos_directos,
            "yacimientos": yacimientos,
            "detalles_hubs": detalles_hubs,
        },
        "intermedias": {
            "inyeccion_std": inyeccion_std,
            "inyeccion": inyeccion,
            "inyeccion_area": inyeccion_area,
            "inyeccion_yacimientos_areas": inyeccion_yacimientos_areas,
            "inyeccion_detalles_hubs": inyeccion_detalles_hubs,
            "inyeccion_flujos_directos": inyeccion_flujos_directos,
        },
        "totales": {
            "tabla_total_yacimientos": tabla_total_yacimientos,
            "tabla_total_flujos_directos": tabla_total_flujos_directos,
            "tabla_total_detalles_hubs": tabla_total_detalles_hubs,
        },
        "plantas": plantas,
        "flujos_plantas": flujos_plantas,
        "desvio_balance": desvio_balance,
        "red_gasoductos": red_gasoductos,
    }


# ===========================================================================
# 6. Registro plano de tablas (para la pestaña de exploración)
# ===========================================================================

def _registro_tablas(res: dict) -> dict:
    """Nombre -> DataFrame de TODO lo que produce el pipeline, aplanado."""
    reg = {}

    for grupo, prefijo in (("inputs", "IN"), ("intermedias", "MID"), ("totales", "TTL")):
        for nombre, df in res.get(grupo, {}).items():
            reg[f"[{prefijo}] {nombre}"] = _a_dataframe(df)

    reg["[PLANTAS] flujos_plantas"] = res["flujos_plantas"].rename_axis("planta").reset_index()

    for nombre, datos in res.get("plantas", {}).items():
        p = _slug(nombre).upper()
        reg[f"[{p}] tabla_total"] = _a_dataframe(datos.get("tabla_total"))
        reg[f"[{p}] gas_rico_IN"] = _a_dataframe(datos.get("gas_rico_IN"), "gas_rico_IN")
        reg[f"[{p}] gas_residual_OUT"] = _a_dataframe(datos.get("gas_residual_OUT"), "gas_residual_OUT")
        reg[f"[{p}] retenidos"] = _a_dataframe(datos.get("retenidos"), "retenidos")
        reg[f"[{p}] retenidos_vol"] = _a_dataframe(datos.get("retenidos_vol"), "retenidos_vol")

    return {k: v for k, v in reg.items() if isinstance(v, pd.DataFrame) and not v.empty}


def _filtrar(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Controles de filtrado sobre un DataFrame: columnas, búsqueda de texto,
    filtro por valores de una columna y rango numérico."""
    if df.empty:
        return df

    c1, c2 = st.columns([2, 1])
    with c1:
        cols = st.multiselect(
            "Columnas", list(df.columns), default=list(df.columns),
            key=f"cols_{key}",
        )
    with c2:
        busqueda = st.text_input(
            "Buscar texto (en todas las columnas)", key=f"busq_{key}",
            placeholder="ej: fortin, aguada",
        )

    out = df[cols] if cols else df

    if busqueda:
        patron = busqueda.strip()
        mascara = out.apply(
            lambda s: s.astype(str).str.contains(patron, case=False, na=False, regex=False)
        ).any(axis=1)
        out = out[mascara]

    cat_cols = [c for c in out.columns if out[c].dtype == "object"]
    num_cols = [c for c in out.columns if pd.api.types.is_numeric_dtype(out[c])]

    f1, f2 = st.columns(2)
    with f1:
        if cat_cols:
            col_cat = st.selectbox(
                "Filtrar por valores de", ["—"] + cat_cols, key=f"cat_{key}"
            )
            if col_cat != "—":
                valores = sorted(out[col_cat].dropna().astype(str).unique().tolist())
                elegidos = st.multiselect(
                    f"Valores de {col_cat}", valores, default=valores,
                    key=f"catval_{key}",
                )
                if elegidos:
                    out = out[out[col_cat].astype(str).isin(elegidos)]
    with f2:
        if num_cols:
            col_num = st.selectbox(
                "Filtrar por rango de", ["—"] + num_cols, key=f"num_{key}"
            )
            if col_num != "—":
                serie = pd.to_numeric(out[col_num], errors="coerce")
                if serie.notna().any():
                    lo, hi = float(serie.min()), float(serie.max())
                    if lo < hi:
                        rango = st.slider(
                            f"Rango de {col_num}", lo, hi, (lo, hi), key=f"rango_{key}"
                        )
                        out = out[serie.between(*rango) | serie.isna()]

    return out


def _panel_tabla(nombre: str, df: pd.DataFrame, key: str):
    """Vista de una tabla con filtros, totales y descarga."""
    filtrada = _filtrar(df, key)

    o1, o2, o3, o4 = st.columns([1, 1, 1, 2])
    with o1:
        decimales = st.number_input("Decimales", 0, 8, 3, key=f"dec_{key}")
    with o2:
        transponer = st.toggle("Transponer", value=False, key=f"tr_{key}")
    with o3:
        totales = st.toggle("Fila de totales", value=True, key=f"tot_{key}")
    with o4:
        num_cols = [c for c in filtrada.columns if pd.api.types.is_numeric_dtype(filtrada[c])]
        ordenar = st.selectbox("Ordenar por", ["—"] + list(filtrada.columns), key=f"ord_{key}")

    vista = filtrada.copy()
    if ordenar != "—":
        vista = vista.sort_values(ordenar, ascending=False, na_position="last")

    if totales and num_cols:
        fila = {c: (vista[c].sum() if c in num_cols else "") for c in vista.columns}
        primera = vista.columns[0]
        if primera not in num_cols:
            fila[primera] = "TOTAL"
        vista = pd.concat([vista, pd.DataFrame([fila])], ignore_index=True)

    if transponer:
        vista = vista.T

    st.caption(
        f"{len(filtrada):,} filas × {len(filtrada.columns)} columnas "
        f"(de {len(df):,} × {len(df.columns)} sin filtrar)"
    )
    st.dataframe(
        vista.style.format(precision=int(decimales), thousands=",")
        if not transponer else vista,
        use_container_width=True,
        height=460,
    )
    _descargar_csv(filtrada, _slug(nombre), key=f"dl_{key}")


# ===========================================================================
# 7. Comparador contra el Excel de referencia
# ===========================================================================

def _comparar_con_excel(df_calc: pd.DataFrame, nombre_tabla: str, key: str):
    st.markdown("**Comparar contra el Excel de referencia**")
    st.caption(
        "Subí el Excel original, elegí la hoja y la clave de apareo. "
        "Se comparan las columnas numéricas que existan en las dos tablas."
    )

    ref_file = st.file_uploader(
        "Excel de referencia (.xlsx)", type=["xlsx", "xlsm"], key=f"ref_{key}"
    )
    if ref_file is None:
        return

    try:
        xls = pd.ExcelFile(ref_file)
    except Exception as e:
        st.error(f"No se pudo abrir el archivo: {e}")
        return

    h1, h2, h3 = st.columns([2, 1, 1])
    with h1:
        hoja = st.selectbox("Hoja", xls.sheet_names, key=f"hoja_{key}")
    with h2:
        header = st.number_input("Fila de encabezado (0 = primera)", 0, 50, 0, key=f"hdr_{key}")
    with h3:
        usecols = st.text_input("Rango de columnas (opcional)", "", key=f"uc_{key}",
                                placeholder="ej: B:AC")

    try:
        df_ref = pd.read_excel(
            xls, sheet_name=hoja, header=int(header),
            usecols=usecols.strip() or None,
        )
    except Exception as e:
        st.error(f"No se pudo leer la hoja: {e}")
        return

    df_ref = df_ref.dropna(axis=1, how="all").dropna(axis=0, how="all")
    with st.expander(f"Ver hoja '{hoja}' cruda ({len(df_ref):,} filas)"):
        st.dataframe(df_ref, use_container_width=True, height=280)

    k1, k2 = st.columns(2)
    with k1:
        clave_calc = st.selectbox(
            "Clave en la tabla calculada", ["(por posición)"] + list(df_calc.columns),
            key=f"kc_{key}",
        )
    with k2:
        clave_ref = st.selectbox(
            "Clave en el Excel", ["(por posición)"] + list(df_ref.columns),
            key=f"kr_{key}",
        )

    # Apareo de columnas numericas por nombre normalizado
    mapa_calc = {_norm_col(c): c for c in df_calc.columns}
    mapa_ref = {_norm_col(c): c for c in df_ref.columns}
    comunes = [
        k for k in mapa_calc
        if k in mapa_ref
        and pd.api.types.is_numeric_dtype(df_calc[mapa_calc[k]])
        and pd.api.types.is_numeric_dtype(df_ref[mapa_ref[k]])
    ]
    if not comunes:
        st.warning(
            "No hay columnas numéricas con nombre equivalente en las dos tablas. "
            "Revisá la fila de encabezado o el rango de columnas."
        )
        return

    elegidas = st.multiselect(
        "Columnas a comparar",
        [mapa_calc[k] for k in comunes],
        default=[mapa_calc[k] for k in comunes],
        key=f"cmp_{key}",
    )
    if not elegidas:
        return

    t1, t2, t3 = st.columns(3)
    with t1:
        tol_rel = st.number_input("Tolerancia relativa (%)", 0.0, 100.0, 0.5, 0.1, key=f"tolr_{key}")
    with t2:
        tol_abs = st.number_input("Tolerancia absoluta", 0.0, 1e9, 0.0, key=f"tola_{key}")
    with t3:
        solo_dif = st.toggle("Solo diferencias", value=True, key=f"sd_{key}")

    # Armado del par calculado / referencia
    if clave_calc != "(por posición)" and clave_ref != "(por posición)":
        izq = df_calc[[clave_calc] + elegidas].copy()
        izq[clave_calc] = izq[clave_calc].astype(str).str.strip().str.lower()
        der_cols = [mapa_ref[_norm_col(c)] for c in elegidas]
        der = df_ref[[clave_ref] + der_cols].copy()
        der[clave_ref] = der[clave_ref].astype(str).str.strip().str.lower()
        der = der.rename(columns={clave_ref: clave_calc})
        der = der.rename(columns={mapa_ref[_norm_col(c)]: f"{c}__ref" for c in elegidas})
        par = izq.merge(der, on=clave_calc, how="outer", indicator=True)
        etiqueta_clave = clave_calc
    else:
        n = min(len(df_calc), len(df_ref))
        izq = df_calc[elegidas].head(n).reset_index(drop=True)
        der = df_ref[[mapa_ref[_norm_col(c)] for c in elegidas]].head(n).reset_index(drop=True)
        der.columns = [f"{c}__ref" for c in elegidas]
        par = pd.concat([izq, der], axis=1)
        par.insert(0, "fila", range(1, n + 1))
        par["_merge"] = "both"
        etiqueta_clave = "fila"
        if len(df_calc) != len(df_ref):
            st.info(
                f"Distinta cantidad de filas: calculada {len(df_calc):,} vs "
                f"Excel {len(df_ref):,}. Se comparan las primeras {n:,}."
            )

    # Diferencias en formato largo: una fila por (clave, columna)
    piezas = []
    for c in elegidas:
        sub = pd.DataFrame({
            etiqueta_clave: par[etiqueta_clave],
            "columna": c,
            "calculado": pd.to_numeric(par[c], errors="coerce"),
            "excel": pd.to_numeric(par[f"{c}__ref"], errors="coerce"),
        })
        sub["diferencia"] = sub["calculado"] - sub["excel"]
        # Con excel == 0 el error relativo no existe: en ese caso manda solo la
        # tolerancia absoluta. Si se rellenara dif_% con 0 esas celdas pasarian
        # siempre, que es justo donde se esconden los desvios grandes.
        base = sub["excel"].abs().mask(lambda s: s == 0)
        sub["dif_%"] = sub["diferencia"].abs() / base * 100
        ambos_vacios = sub["calculado"].isna() & sub["excel"].isna()
        ok_abs = (sub["diferencia"].abs() <= tol_abs).fillna(False)
        ok_rel = (sub["dif_%"] <= tol_rel).fillna(False)
        sub["ok"] = ok_abs | ok_rel | ambos_vacios
        piezas.append(sub)

    dif = pd.concat(piezas, ignore_index=True)
    fuera = dif[~dif["ok"]]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Celdas comparadas", f"{len(dif):,}")
    m2.metric("Fuera de tolerancia", f"{len(fuera):,}")
    m3.metric(
        "Máx. diferencia abs.",
        _fmt(dif["diferencia"].abs().max(), 4) if len(dif) else "—",
    )
    m4.metric(
        "Máx. diferencia %",
        _fmt(dif["dif_%"].max(), 2, "%") if dif["dif_%"].notna().any() else "—",
    )

    if len(fuera) == 0:
        st.success(f"✅ Todo dentro de tolerancia ({tol_rel}% / {tol_abs}).")
    else:
        peores = (
            fuera.groupby("columna")
            .agg(celdas=("ok", "size"), max_dif_pct=("dif_%", "max"),
                 max_dif_abs=("diferencia", lambda s: s.abs().max()))
            .sort_values("celdas", ascending=False)
        )
        st.warning(f"⚠️ {len(fuera):,} celdas fuera de tolerancia.")
        st.markdown("**Columnas con más diferencias**")
        st.dataframe(peores.reset_index(), use_container_width=True)

    mostrar = fuera if solo_dif else dif
    mostrar = mostrar.sort_values("dif_%", ascending=False, na_position="last")
    st.dataframe(
        mostrar.style.format(
            {"calculado": "{:,.4f}", "excel": "{:,.4f}",
             "diferencia": "{:,.4f}", "dif_%": "{:,.2f}"}
        ),
        use_container_width=True,
        height=420,
    )
    _descargar_csv(dif, f"diff_{_slug(nombre_tabla)}", key=f"dldiff_{key}",
                   label="⬇️ Descargar comparación completa")


# ===========================================================================
# 8. Sidebar
# ===========================================================================

st.title("Balance de Gas")
st.caption("Pipeline de balance, modelado de plantas y contraste contra el Excel de referencia.")

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
    value=getattr(config, "FECHA_PM_TTY_TBX", config.PERIODO_CONSIDERADO).strftime("%m-%Y"),
    help="Fecha de puesta en marcha / cambio de criterio en TTY-TBX.",
)
try:
    fecha_pm_ts = pd.Timestamp(fecha_pm_str.replace("/", "-"))
except Exception:
    st.sidebar.error("Formato inválido, se usa el default de config.")
    fecha_pm_ts = getattr(config, "FECHA_PM_TTY_TBX", config.PERIODO_CONSIDERADO)

st.sidebar.header("3. Capacidad de ingreso de gas")
cap_tty_tbx = st.sidebar.number_input("TTY-TBX", value=float(config.CAPACIDAD_TTY_TBX), step=1.0)
cap_tty_dp = st.sidebar.number_input("TTY-DP", value=float(config.CAPACIDAD_TTY_DP), step=1.0)
cap_mega = st.sidebar.number_input("MEGA", value=float(config.CAPACIDAD_MEGA), step=1.0)
cap_base_tbx = st.sidebar.number_input(
    "TBX base convertible", value=float(config.CAPACIDAD_BASE_CONVERTIBLE_TBX), step=0.1
)
cap_adic_tbx = st.sidebar.number_input(
    "TBX adicional", value=float(config.CAPACIDAD_ADICIONAL_TBX), step=0.1
)

st.sidebar.header("4. Evacuación de LGN")
st.sidebar.caption("Es la restricción activa del modelo, no el ingreso de gas.")
evac_tty_tbx = st.sidebar.number_input(
    "TTY-TBX", value=float(config.CAPACIDAD_EVACUACION_TTY_TBX), step=100.0, key="e_tbx"
)
evac_tty_dp = st.sidebar.number_input(
    "TTY-DP", value=float(config.CAPACIDAD_EVACUACION_TTY_DP), step=10.0, key="e_dp"
)
evac_mega = st.sidebar.number_input(
    "MEGA", value=float(config.CAPACIDAD_EVACUACION_MEGA), step=100.0, key="e_mega"
)

st.sidebar.header("5. Derivaciones y factores")
max_deriv_tbx_dp = st.sidebar.number_input(
    "Máx. TTY-TBX → TTY-DP",
    value=float(cap_tty_dp - cap_base_tbx),
    step=0.5,
    help="Default derivado: CAPACIDAD_TTY_DP − CAPACIDAD_BASE_CONVERTIBLE_TBX.",
)
max_deriv_dp_mega = st.sidebar.number_input(
    "Máx. TTY-DP → MEGA", value=float(config.MAX_DERIVACION_TTY_DP_A_MEGA), step=0.5
)
with st.sidebar.expander("Factores de retenidos (unidades)"):
    st.caption(
        "Convierte retenidos_vol a la unidad de la capacidad de evacuación. "
        "Si el factor está mal, fracción tratable sale mal."
    )
    fr_tbx = st.number_input("TTY-TBX", value=float(config.FACTOR_RETENIDOS_TTY_TBX), format="%.6f")
    fr_dp = st.number_input("TTY-DP", value=float(config.FACTOR_RETENIDOS_TTY_DP), format="%.6f")
    fr_mega = st.number_input("MEGA", value=float(config.FACTOR_RETENIDOS_MEGA), format="%.6f")

st.sidebar.header("6. Unidades de la vista")
unidad_gas = st.sidebar.text_input("Unidad de volumen de gas", "MMm3/d")
unidad_lgn = st.sidebar.text_input("Unidad de LGN", "unid. retenidos")

st.sidebar.header("7. Salidas")
guardar_csvs = st.sidebar.checkbox("Guardar CSVs en disco al ejecutar", value=False)

run = st.sidebar.button("▶️ Ejecutar pipeline", type="primary", use_container_width=True)

PARAMS = {
    "PERIODO_CONSIDERADO": periodo_ts,
    "FECHA_PM_TTY_TBX": fecha_pm_ts,
    "CAPACIDAD_TTY_TBX": cap_tty_tbx,
    "CAPACIDAD_TTY_DP": cap_tty_dp,
    "CAPACIDAD_MEGA": cap_mega,
    "CAPACIDAD_BASE_CONVERTIBLE_TBX": cap_base_tbx,
    "CAPACIDAD_ADICIONAL_TBX": cap_adic_tbx,
    "CAPACIDAD_EVACUACION_TTY_TBX": evac_tty_tbx,
    "CAPACIDAD_EVACUACION_TTY_DP": evac_tty_dp,
    "CAPACIDAD_EVACUACION_MEGA": evac_mega,
    "MAX_DERIVACION_TTY_TBX_A_TTY_DP": max_deriv_tbx_dp,
    "MAX_DERIVACION_TTY_DP_A_MEGA": max_deriv_dp_mega,
    "FACTOR_RETENIDOS_TTY_TBX": fr_tbx,
    "FACTOR_RETENIDOS_TTY_DP": fr_dp,
    "FACTOR_RETENIDOS_MEGA": fr_mega,
}

if run:
    try:
        st.session_state["resultados"] = ejecutar_pipeline(input_path, PARAMS, guardar_csvs)
        st.sidebar.success("Pipeline ejecutado.")
    except Exception as e:
        st.sidebar.error(f"El pipeline falló: {e}")
        st.exception(e)


# ===========================================================================
# 9. Resultados
# ===========================================================================

resultados = st.session_state.get("resultados")

if resultados is None:
    st.info("Elegí los parámetros en la barra lateral y apretá **Ejecutar pipeline**.")
    st.stop()

plantas = resultados["plantas"]
flujos_plantas = resultados["flujos_plantas"]

tabs = st.tabs(
    ["Resumen", "Tablas", "Cadena de gas"] + list(plantas.keys())
)

# --- Resumen ---------------------------------------------------------------
with tabs[0]:
    desvio = resultados["desvio_balance"]
    if desvio < 1e-6:
        st.success(f"Balance por planta cerrado (desvío máx. {desvio:.2e}).")
    else:
        st.error(
            f"El balance no cierra: desvío máx. {desvio:,.6f}. "
            "Revisá calcular_flujos_planta — debería valer "
            "entrante = procesado + derivado + bypass."
        )

    for nombre, datos in plantas.items():
        f = datos.get("flujos", {}) or {}
        entrante = _num(f.get("vol_entrante"))
        cap = _num(datos.get("capacidad_ingreso"))
        lgn = _num(f.get("lgn_potencial"))
        evac = _num(datos.get("capacidad_evacuacion"))

        st.markdown(f"### {nombre}")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Gas entrante", _fmt(entrante, 2), help=unidad_gas)
        c2.metric(
            "Ocupación ingreso",
            _fmt(entrante / cap * 100, 0, "%") if cap else "—",
            help=f"Capacidad {_fmt(cap, 1)} {unidad_gas}",
        )
        c3.metric("Procesado", _fmt(_num(f.get("vol_procesado")), 2))
        c4.metric("Derivado", _fmt(_num(f.get("vol_derivado")), 2))
        c5.metric("Bypass", _fmt(_num(f.get("bypass")), 2))

        if evac and lgn > evac:
            st.warning(
                f"LGN potencial {_fmt(lgn, 1)} supera la evacuación {_fmt(evac, 1)} "
                f"→ se trata el {_fmt(_num(f.get('fraccion_tratable')) * 100, 1, '%')} del gas."
            )
        elif evac:
            st.success(f"LGN potencial {_fmt(lgn, 1)} dentro de la evacuación {_fmt(evac, 1)}.")
        st.divider()

    st.markdown("### Flujos por planta")
    st.dataframe(
        flujos_plantas.style.format(precision=3, thousands=","),
        use_container_width=True,
    )
    _descargar_csv(flujos_plantas.rename_axis("planta").reset_index(), "flujos_plantas", key="dl_flujos")

# --- Tablas ----------------------------------------------------------------
with tabs[1]:
    registro = _registro_tablas(resultados)

    st.markdown("#### Explorador de tablas")
    izq, der = st.columns([2, 1])
    with izq:
        nombre_tabla = st.selectbox("Tabla", list(registro.keys()), key="sel_tabla")
    with der:
        st.metric("Tablas disponibles", len(registro))

    df_sel = registro[nombre_tabla]
    _panel_tabla(nombre_tabla, df_sel, key=_slug(nombre_tabla))

    st.divider()
    _comparar_con_excel(df_sel, nombre_tabla, key=_slug(nombre_tabla))

    st.divider()
    with st.expander("Ver dos tablas lado a lado"):
        s1, s2 = st.columns(2)
        with s1:
            n_a = st.selectbox("Izquierda", list(registro.keys()), key="lado_a")
            st.dataframe(registro[n_a], use_container_width=True, height=420)
        with s2:
            n_b = st.selectbox(
                "Derecha", list(registro.keys()),
                index=min(1, len(registro) - 1), key="lado_b",
            )
            st.dataframe(registro[n_b], use_container_width=True, height=420)

# --- Cadena de gas ---------------------------------------------------------
with tabs[2]:
    st.markdown("#### Cadena de plantas y derivaciones")
    st.caption("TTY-TBX → TTY-DP → MEGA. El excedente se deriva; lo que no entra, bypasea.")
    st.graphviz_chart(_dot_cadena_plantas(plantas), use_container_width=True)

    st.divider()
    st.markdown("#### Áreas hacia gasoductos")
    edges = resultados.get("red_gasoductos")
    if edges is None or edges.empty:
        st.info(
            "No hay columnas Area / Gasoducto / Volumen_inyectado en "
            "tabla_total_yacimientos, así que no se puede armar la red."
        )
    else:
        top_n = st.slider("Mostrar los N arcos de mayor volumen", 5, 60, 25)
        st.graphviz_chart(_dot_red_gasoductos(edges, top_n=top_n), use_container_width=True)
        with st.expander("Ver aristas"):
            st.dataframe(edges, use_container_width=True, height=320)

# --- Una tab por planta ----------------------------------------------------
for tab, (nombre, datos) in zip(tabs[3:], plantas.items()):
    with tab:
        flujos = datos.get("flujos", {}) or {}
        retenidos_vol = datos.get("retenidos_vol")

        cortes = {}
        if isinstance(retenidos_vol, pd.DataFrame):
            for corte in ("etano", "propano", "butanos", "gasolina"):
                if corte in retenidos_vol.columns:
                    cortes[corte] = _num(retenidos_vol[corte]) * _num(
                        datos.get("factor_retenidos"), 1.0
                    )

        svg = _svg_esquema_planta(
            nombre_planta=nombre,
            flujos=flujos,
            capacidad_ingreso=datos.get("capacidad_ingreso"),
            capacidad_evacuacion=datos.get("capacidad_evacuacion"),
            lgn_cortes=cortes,
            unidad_gas=unidad_gas,
            unidad_lgn=unidad_lgn,
            color_planta=datos.get("color", "#1F7A6B"),
            destino_derivacion=datos.get("destino_derivacion"),
        )
        _mostrar_svg(svg, alto=450, key=_slug(nombre))
        st.download_button(
            "⬇️ Descargar esquema (.svg)",
            data=svg,
            file_name=f"esquema_{_slug(nombre)}.svg",
            mime="image/svg+xml",
            key=f"svg_{_slug(nombre)}",
        )

        deriv_in = datos.get("derivacion_entrante")
        if deriv_in:
            st.info(
                f"Derivación entrante desde `{deriv_in.get('origen', '?')}`: "
                f"{_fmt(_num(deriv_in.get('vol_derivacion')), 2)} {unidad_gas}. "
                "Entra como fila de input, así pesa en la mezcla de gas_rico_IN."
            )

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Gas rico IN (fracción molar)**")
            st.dataframe(
                _a_dataframe(datos.get("gas_rico_IN"), "gas_rico_IN"),
                use_container_width=True, height=380,
            )
        with c2:
            st.markdown("**Gas residual OUT (fracción molar)**")
            st.dataframe(
                _a_dataframe(datos.get("gas_residual_OUT"), "gas_residual_OUT"),
                use_container_width=True, height=380,
            )

        st.markdown("**LGN retenido por corte**")
        st.dataframe(_a_dataframe(retenidos_vol, "retenidos_vol"), use_container_width=True)

        with st.expander("Ver tabla de input de la planta"):
            tabla = _a_dataframe(datos.get("tabla_total"))
            st.dataframe(tabla, use_container_width=True, height=420)
            _descargar_csv(tabla, f"tabla_{_slug(nombre)}", key=f"dltab_{_slug(nombre)}")
