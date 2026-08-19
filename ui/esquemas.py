"""
Esquema de bloques de una planta (SVG).

Reemplaza a `_svg_esquema_planta` de app.py manteniendo EXACTAMENTE la misma
firma, así `_armar_esquema()` sigue funcionando sin tocar nada.

POR QUE ANTES SE VEIA ROTO
--------------------------
1. `st.markdown(svg, unsafe_allow_html=True)`: el string arrancaba con salto de
   línea + 4 espacios de indentación, así que Markdown lo tomaba como bloque de
   código y lo imprimía como texto. Y aun sin la indentación, el sanitizador de
   Markdown se come parte del SVG.
   -> Ahora: `textwrap.dedent().strip()` + render como data-URI dentro de
      `components.html`, que no pasa por Markdown.

2. Los tres esquemas usaban el mismo `id="arrow"` para el `<marker>`. En una
   misma página los IDs colisionan y las flechas desaparecen.
   -> Ahora: `id="flecha_{slug de la planta}"`, único por planta.

3. El `<defs>` estaba al final, después de los elementos que lo referencian.
   -> Ahora: `<defs>` primero.

4. La geometría se solapaba (el ByPass cruzaba la caja, los textos de IN/OUT
   pisaban las flechas, el marker apuntaba al extremo equivocado).
   -> Ahora: entradas a la izquierda, salidas a la derecha, LGN arriba,
      ByPass abajo. Sin superposiciones.
"""

import base64
import re
import textwrap

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# Tinta y acentos. El color de la caja lo sigue eligiendo app.py (color_planta).
_TINTA = "#1a1a1a"
_TEXTO = "#2c3e50"
_SUAVE = "#7f8c8d"
_TRASPASO = "#B9770E"
_BYPASS = "#C0392B"


def _slug(texto) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(texto).lower()).strip("_")


def _fmt(valor, decimales=1, unidad=""):
    """Número formateado, o '—' si todavía no hay dato."""
    if valor is None:
        return "—"
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if pd.isna(f):
        return "—"
    return f"{f:,.{decimales}f}{unidad}"


def svg_esquema_planta(
    nombre_planta: str,
    color_planta: str = "#5DADE2",
    flujo_in=None,
    flujo_in_eq=None,
    flujo_out=None,
    flujo_out_eq=None,
    bypass=None,
    bypass_eq=None,
    derivacion_in=None,
    derivacion_out=None,
    rtp=None,
    liq_total=None,
    etano=None,
    propano=None,
    butanos=None,
    gasolina=None,
    ratio_in_out=None,
    activa=True,
) -> str:
    """Diagrama de bloques de la planta, en el estilo de la lámina de referencia.

    Entradas a la izquierda (gas disponible + traspaso recibido), salidas a la
    derecha (gas residual + traspaso a la siguiente), LGN arriba, ByPass abajo.

    Firma compatible con `_armar_esquema()` de app.py. `activa` es opcional: si
    viene en False la caja se dibuja punteada y en gris (planta fuera de
    servicio pre-PM).
    """
    uid = _slug(nombre_planta)
    fill_caja = color_planta if activa else "#EAEDED"
    dash_caja = "" if activa else ' stroke-dasharray="6,4"'
    sufijo_estado = "" if activa else "  ·  FUERA DE SERVICIO"

    svg = f"""
<svg viewBox="0 0 760 470" xmlns="http://www.w3.org/2000/svg"
     font-family="Arial, Helvetica, sans-serif" style="width:100%;height:auto;">

  <defs>
    <marker id="flecha_{uid}" markerWidth="9" markerHeight="9" refX="7" refY="4.5"
            orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L9,4.5 L0,9 Z" fill="{_TINTA}"/>
    </marker>
    <marker id="flecha_tr_{uid}" markerWidth="9" markerHeight="9" refX="7" refY="4.5"
            orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L9,4.5 L0,9 Z" fill="{_TRASPASO}"/>
    </marker>
    <marker id="flecha_by_{uid}" markerWidth="9" markerHeight="9" refX="7" refY="4.5"
            orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L9,4.5 L0,9 Z" fill="{_BYPASS}"/>
    </marker>
  </defs>

  <!-- ============ LGN retenido (arriba, centrado) ============ -->
  <rect x="250" y="26" width="262" height="128" fill="#FFFFFF"
        stroke="{_SUAVE}" stroke-width="1" rx="3"/>

  <text x="266" y="50" font-size="14" font-weight="bold" fill="{_TINTA}">Liq. total</text>
  <text x="496" y="50" font-size="14" font-weight="bold" text-anchor="end"
        fill="{_TINTA}">{_fmt(liq_total)} tn/d</text>
  <line x1="262" y1="60" x2="500" y2="60" stroke="{_SUAVE}" stroke-width="0.8"/>

  <text x="278" y="80" font-size="13" fill="{_TEXTO}">Etano</text>
  <text x="496" y="80" font-size="13" text-anchor="end" fill="{_TINTA}">{_fmt(etano)} tn/d</text>

  <text x="278" y="100" font-size="13" fill="{_TEXTO}">Propano</text>
  <text x="496" y="100" font-size="13" text-anchor="end" fill="{_TINTA}">{_fmt(propano)} tn/d</text>

  <text x="278" y="120" font-size="13" fill="{_TEXTO}">Butanos</text>
  <text x="496" y="120" font-size="13" text-anchor="end" fill="{_TINTA}">{_fmt(butanos)} tn/d</text>

  <text x="278" y="140" font-size="13" fill="{_TEXTO}">Gasolina</text>
  <text x="496" y="140" font-size="13" text-anchor="end" fill="{_TINTA}">{_fmt(gasolina)} tn/d</text>

  <!-- RTP -->
  <text x="540" y="80" font-size="13" font-weight="bold" fill="{_TINTA}">RTP</text>
  <text x="540" y="100" font-size="13" fill="{_TEXTO}">{_fmt(rtp)} tn/d</text>

  <!-- flecha LGN: sale de la caja hacia arriba -->
  <line x1="381" y1="223" x2="381" y2="160" stroke="{_TINTA}" stroke-width="1.6"
        marker-end="url(#flecha_{uid})"/>

  <!-- ============ Caja de la planta ============ -->
  <rect x="240" y="225" width="280" height="106" fill="{fill_caja}"
        stroke="{_TINTA}" stroke-width="1.6" rx="3"{dash_caja}/>
  <text x="380" y="285" font-size="16" font-weight="bold" text-anchor="middle"
        fill="#0b2545">{nombre_planta.upper()}{sufijo_estado}</text>

  <!-- ============ Entradas (izquierda) ============ -->
  <line x1="20" y1="248" x2="236" y2="248" stroke="{_TINTA}" stroke-width="4"
        marker-end="url(#flecha_{uid})"/>
  <text x="20" y="236" font-size="10.5" letter-spacing="1"
        fill="{_SUAVE}">GAS DISPONIBLE</text>
  <text x="20" y="268" font-size="14" font-weight="bold"
        fill="{_TINTA}">{_fmt(flujo_in, 2)} MMm3/d</text>
  <text x="20" y="284" font-size="11.5" fill="{_TEXTO}">{_fmt(flujo_in_eq, 2)} MMm3eq/d</text>

  <line x1="20" y1="308" x2="236" y2="308" stroke="{_TRASPASO}" stroke-width="2.5"
        marker-end="url(#flecha_tr_{uid})"/>
  <text x="20" y="300" font-size="10.5" letter-spacing="1"
        fill="{_SUAVE}">TRASPASO RECIBIDO</text>
  <text x="20" y="328" font-size="13" font-weight="bold"
        fill="{_TRASPASO}">{_fmt(derivacion_in, 2)} MMm3/d</text>

  <!-- ============ Salidas (derecha) ============ -->
  <line x1="524" y1="248" x2="740" y2="248" stroke="{_TINTA}" stroke-width="4"
        marker-end="url(#flecha_{uid})"/>
  <text x="534" y="236" font-size="10.5" letter-spacing="1"
        fill="{_SUAVE}">GAS RESIDUAL OUT</text>
  <text x="534" y="268" font-size="14" font-weight="bold"
        fill="{_TINTA}">{_fmt(flujo_out, 2)} MMm3/d</text>
  <text x="534" y="284" font-size="11.5" fill="{_TEXTO}">{_fmt(flujo_out_eq, 2)} MMm3eq/d</text>

  <line x1="524" y1="308" x2="740" y2="308" stroke="{_TRASPASO}" stroke-width="2.5"
        marker-end="url(#flecha_tr_{uid})"/>
  <text x="534" y="300" font-size="10.5" letter-spacing="1"
        fill="{_SUAVE}">TRASPASO A LA SIGUIENTE</text>
  <text x="534" y="328" font-size="13" font-weight="bold"
        fill="{_TRASPASO}">{_fmt(derivacion_out, 2)} MMm3/d</text>

  <!-- ============ ByPass (abajo) ============ -->
  <line x1="460" y1="333" x2="460" y2="392" stroke="{_BYPASS}" stroke-width="2"
        stroke-dasharray="5,4"/>
  <line x1="460" y1="392" x2="740" y2="392" stroke="{_BYPASS}" stroke-width="2"
        stroke-dasharray="5,4" marker-end="url(#flecha_by_{uid})"/>
  <text x="472" y="384" font-size="10.5" letter-spacing="1" fill="{_SUAVE}">BYPASS</text>
  <text x="472" y="412" font-size="13" font-weight="bold"
        fill="{_BYPASS}">{_fmt(bypass, 2)} MMm3/d</text>
  <text x="472" y="428" font-size="11.5" fill="{_TEXTO}">{_fmt(bypass_eq, 2)} MMm3eq/d</text>

  <!-- ============ Ratio IN / OUT ============ -->
  <rect x="20" y="404" width="260" height="34" fill="#FFFFFF"
        stroke="{_TINTA}" stroke-width="1.2" rx="3"/>
  <text x="34" y="426" font-size="12.5" fill="{_TINTA}">Ratio IN / OUT</text>
  <text x="196" y="426" font-size="13" font-weight="bold" text-anchor="end"
        fill="{_TINTA}">{_fmt(ratio_in_out, 3)}</text>
  <text x="206" y="426" font-size="10.5" fill="{_TEXTO}">m3std/m3std</text>
</svg>
"""
    return textwrap.dedent(svg).strip()


def mostrar_esquema_planta(nombre_planta: str, color_planta: str = "#5DADE2",
                           alto: int = 470, descargable: bool = True, **campos):
    """Dibuja el esquema y (opcional) ofrece el .svg para descargar.

    Uso en app.py:
        mostrar_esquema_planta(
            nombre_planta=nombre_planta,
            color_planta=datos.get("color", "#5DADE2"),
            **_armar_esquema(datos),
        )

    El render va como data-URI dentro de un iframe con fondo blanco fijo: así no
    pasa por el sanitizador de Markdown y se lee igual en tema claro y oscuro.
    """
    svg = svg_esquema_planta(nombre_planta=nombre_planta,
                            color_planta=color_planta, **campos)

    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    components.html(
        '<div style="width:100%;background:#FFFFFF;border:1px solid #E3E8EA;'
        'border-radius:6px;padding:10px 12px;box-sizing:border-box;">'
        f'<img style="width:100%;height:auto;" src="data:image/svg+xml;base64,{b64}"/>'
        "</div>",
        height=alto + 30,
        scrolling=False,
    )

    if descargable:
        st.download_button(
            "⬇️ Descargar esquema (.svg)",
            data=svg,
            file_name=f"esquema_{_slug(nombre_planta)}.svg",
            mime="image/svg+xml",
            key=f"svg_{_slug(nombre_planta)}",
        )

    return svg
