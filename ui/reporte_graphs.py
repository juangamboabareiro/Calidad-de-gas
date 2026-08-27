"""
Reporte PDF del tab Graphs — las láminas del dashboard, para imprimir/enviar.
=============================================================================

Genera un PDF multi-página (A4 apaisado) con las mismas vistas que el tab
Graphs, en el orden del dashboard del cliente: lámina objetivo (inyección a
transporte + calidad de la mezcla), producción (inyección por área y detalle
por gasoducto), calidad por gasoducto, tratamiento (procesado/BP y retenidos
por corte), calidad por planta (PCS/IW in-out), caudal vs capacidad y el
resumen anual.

POR QUÉ MATPLOTLIB Y NO ALTAIR
------------------------------
Los gráficos del tab son Altair (Vega-Lite), que se renderiza en el BROWSER.
Exportarlos a imagen del lado del servidor exige `vl-convert` + binarios que
en Streamlit Cloud fallan seguido. Acá las láminas se redibujan con matplotlib
directo desde `serie`, que es la misma fuente de datos: el PDF no depende del
navegador ni de binarios externos. El costo es mantener dos representaciones;
el criterio es que el TAB es interactivo y el PDF es una foto.

Requiere `matplotlib` en requirements.txt.

Uso:
    from ui.reporte_graphs import generar_reporte_pdf
    pdf_bytes = generar_reporte_pdf(serie)  # serie = dict del ejecutar_serie
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

import matplotlib
matplotlib.use("Agg")  # backend sin display: corre en el server
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

# A4 vertical (retrato) en pulgadas.
_HOJA = (8.27, 11.69)
_COLORES_TPE = {"MEGA": "#1F3B5C", "TTY": "#2E86C1",
                "Directo a gasoducto": "#5D8233"}
_CORTES = {"lgn_etano": "C2", "lgn_propano": "C3",
           "lgn_butanos": "C4", "lgn_gasolina": "C5+"}

_FILAS_RESUMEN = {
    "Gas tratado [MMm3/d]": ("vol_asignado", "{:,.2f}"),
    "Retenidos [tn/d]": ("lgn_asignado", "{:,.0f}"),
    "PCS entrada [kcal/m3]": ("pcs_in", "{:,.0f}"),
    "PCS salida [kcal/m3]": ("pcs_out", "{:,.0f}"),
    "IW entrada [kcal/m3]": ("iw_in", "{:,.0f}"),
    "IW salida [kcal/m3]": ("iw_out", "{:,.0f}"),
}


# ===========================================================================
# Helpers
# ===========================================================================

def _fmt_eje_fechas(ax):
    ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(True, alpha=0.25, linewidth=0.5)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)


def _top_n_mas_otros(df, col_cat, col_val, top_n=10):
    """Top-N categorías por volumen total, el resto fundido en 'Otros'."""
    orden = df.groupby(col_cat)[col_val].sum().sort_values(ascending=False)
    top = set(orden.head(top_n).index)
    d = df[["periodo", col_cat, col_val]].copy()
    d[col_cat] = d[col_cat].where(d[col_cat].isin(top), "Otros")
    return d.groupby(["periodo", col_cat], as_index=False)[col_val].sum()


def _apilada(ax, df, col_cat, col_val, titulo, unidad="MMm3/d"):
    """Área apilada por categoría; 'Otros' al final, paleta tab20."""
    pivot = (df.pivot_table(index="periodo", columns=col_cat, values=col_val,
                            aggfunc="sum").fillna(0.0).sort_index())
    if pivot.empty:
        ax.set_axis_off()
        ax.set_title(f"{titulo}\n(sin datos)", fontsize=9)
        return
    cols = sorted(pivot.columns, key=lambda c: (c == "Otros", str(c)))
    pivot = pivot[cols]
    colores = plt.cm.tab20.colors
    ax.stackplot(pivot.index, [pivot[c].values for c in cols],
                 labels=[str(c) for c in cols],
                 colors=[colores[i % len(colores)] for i in range(len(cols))],
                 alpha=0.9)
    ax.set_title(titulo, fontsize=10, fontweight="bold")
    ax.set_ylabel(unidad, fontsize=8)
    ax.legend(fontsize=6.5, ncol=2, loc="upper left", framealpha=0.6)
    _fmt_eje_fechas(ax)


def _lineas_max(ax, df_largo, titulo, unidad="kcal/m3", maximos=None):
    for serie, grupo in df_largo.groupby("serie"):
        g = grupo.sort_values("periodo")
        ax.plot(g["periodo"], g["valor"], linewidth=1.8, label=str(serie))
    for etiqueta, valor in (maximos or {}).items():
        if valor and valor > 0:
            ax.axhline(valor, linestyle="--", linewidth=1.0, color="#1a1a1a")
            ax.annotate(etiqueta, xy=(0.995, valor), xycoords=("axes fraction", "data"),
                        fontsize=6.5, ha="right", va="bottom")
    ax.set_title(titulo, fontsize=10, fontweight="bold")
    ax.set_ylabel(unidad, fontsize=8)
    ax.legend(fontsize=7, framealpha=0.6)
    _fmt_eje_fechas(ax)


def _pagina_titulo(pdf, fig_fn):
    """Dibuja una página; si algo revienta, deja una página con el error en
    lugar de abortar el reporte entero (un dato raro en UNA lámina no puede
    costar el PDF completo)."""
    fig = plt.figure(figsize=_HOJA)
    try:
        fig_fn(fig)
    except Exception as e:  # noqa: BLE001
        fig.clf()
        fig.text(0.5, 0.5, f"Esta lámina no se pudo generar:\n{type(e).__name__}: {e}",
                 ha="center", va="center", fontsize=11)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


# ===========================================================================
# Láminas
# ===========================================================================

def _lam_portada(fig, plantas_df, mezcla, unidad_label="MMm³/d STD"):
    fig.text(0.5, 0.62, "Balance de Gas — Reporte", ha="center",
             fontsize=26, fontweight="bold")
    per = pd.to_datetime(plantas_df["periodo"])
    rango = f"{per.min():%m-%Y} a {per.max():%m-%Y}  ·  {per.nunique()} período(s)"
    fig.text(0.5, 0.54, rango, ha="center", fontsize=13)
    fig.text(0.5, 0.48, f"Generado el {datetime.now():%d-%m-%Y %H:%M}  ·  "
                        f"Volúmenes en {unidad_label}",
             ha="center", fontsize=10, color="#555555")

    lineas = []
    tratado = plantas_df.groupby("periodo")["vol_asignado"].sum().mean()
    lineas.append(f"Gas tratado promedio: {tratado:,.2f} ({unidad_label})")
    if "lgn_asignado" in plantas_df.columns:
        lgn = plantas_df.groupby("periodo")["lgn_asignado"].sum().mean()
        lineas.append(f"LGN promedio: {lgn:,.0f} tn/d")
    if mezcla is not None and len(mezcla) and "pcs" in mezcla.columns \
            and mezcla["pcs"].notna().any():
        lineas.append(f"PCS promedio de la mezcla: {mezcla['pcs'].mean():,.0f} kcal/m3")
        if "iw" in mezcla.columns and mezcla["iw"].notna().any():
            lineas.append(f"IW promedio de la mezcla: {mezcla['iw'].mean():,.0f} kcal/m3")
    fig.text(0.5, 0.34, "\n".join(lineas), ha="center", fontsize=11,
             linespacing=1.8)


def _lam_transporte(fig, mezcla, pcs_max, iw_max):
    ax1, ax2 = fig.subplots(2, 1)
    fig.suptitle("Lámina objetivo — sistema de transporte", fontsize=12,
                 fontweight="bold")

    etiquetas = {"vol_mega": "MEGA", "vol_tty": "TTY",
                 "vol_directo_a_gasoducto": "Directo a gasoducto"}
    cols = [c for c in etiquetas if c in mezcla.columns]
    m = mezcla.sort_values("periodo")
    if cols and m[cols].notna().any().any():
        datos = m[cols].fillna(0.0)
        ax1.stackplot(m["periodo"], [datos[c].values for c in cols],
                      labels=[etiquetas[c] for c in cols],
                      colors=[_COLORES_TPE[etiquetas[c]] for c in cols], alpha=0.9)
        ax1.legend(fontsize=7, loc="upper left", framealpha=0.6)
        ax1.set_title("Inyección a sistema de tpe [MMm3/d STD]", fontsize=10,
                      fontweight="bold")
        ax1.set_ylabel("MMm3/d", fontsize=8)
        _fmt_eje_fechas(ax1)
    else:
        ax1.set_axis_off()
        ax1.set_title("Inyección a tpe (sin datos)", fontsize=9)

    if "pcs" in m.columns and m["pcs"].notna().any():
        largo = m.melt(id_vars="periodo",
                       value_vars=[c for c in ("pcs", "iw") if c in m.columns],
                       var_name="serie", value_name="valor").dropna(subset=["valor"])
        largo["serie"] = largo["serie"].map({"pcs": "PCS", "iw": "IW"})
        _lineas_max(ax2, largo, "Calidad del gas de la mezcla [kcal/m3]",
                    maximos={"PCS MAX": pcs_max, "IW MAX": iw_max})
    else:
        ax2.set_axis_off()
        ax2.set_title("Calidad de la mezcla (sin datos)", fontsize=9)


def _lam_produccion(fig, areas):
    fig.suptitle("Producción — inyección", fontsize=12, fontweight="bold")
    origenes = [("yacimientos", "Inyección por área"),
                ("detalles_hubs", "Inyección por HUB"),
                ("hubs", "Entrega de HUBs a plantas")]
    presentes = [(o, t) for o, t in origenes
                 if len(areas) and (areas["origen"] == o).any()]
    if not presentes:
        fig.text(0.5, 0.5, "La serie no trae detalle por área.", ha="center")
        return
    ejes = fig.subplots(len(presentes), 1, squeeze=False)[:, 0]
    for ax, (origen, titulo) in zip(ejes, presentes):
        df = areas[(areas["origen"] == origen) & areas["volumen"].notna()]
        _apilada(ax, _top_n_mas_otros(df, "area", "volumen", 10),
                 "area", "volumen", titulo)


def _lam_gasoductos(fig, areas):
    fig.suptitle("Detalle inyección por gasoducto (top 4 por volumen)",
                 fontsize=12, fontweight="bold")
    df = areas[areas["gasoducto"].notna() & areas["volumen"].notna()]
    if df.empty:
        fig.text(0.5, 0.5, "Sin detalle por gasoducto.", ha="center")
        return
    top = (df.groupby("gasoducto")["volumen"].sum()
             .sort_values(ascending=False).head(4).index.tolist())
    ejes = fig.subplots(4, 1).ravel()
    for ax, gd in zip(ejes, top):
        _apilada(ax, _top_n_mas_otros(df[df["gasoducto"] == gd],
                                      "area", "volumen", 8),
                 "area", "volumen", str(gd))
    for ax in ejes[len(top):]:
        ax.set_axis_off()
    fig.tight_layout(rect=(0, 0, 1, 0.95))


def _lam_calidad_gasoducto(fig, areas):
    ax = fig.subplots()
    df = areas[areas["gasoducto"].notna() & areas["volumen"].notna()]
    if "pcs" not in df.columns or df["pcs"].notna().sum() == 0:
        ax.set_axis_off()
        fig.text(0.5, 0.5, "Las tablas no traen PCS por fila.", ha="center")
        return
    d = df[df["pcs"].notna()].copy()
    d["pcs_x_vol"] = d["pcs"] * d["volumen"]
    pond = d.groupby(["periodo", "gasoducto"], as_index=False).agg(
        pcs_x_vol=("pcs_x_vol", "sum"), volumen=("volumen", "sum"))
    pond = pond[pond["volumen"] > 0]
    pond["valor"] = pond["pcs_x_vol"] / pond["volumen"]
    top = (pond.groupby("gasoducto")["volumen"].sum()
               .sort_values(ascending=False).head(6).index)
    largo = pond[pond["gasoducto"].isin(top)].rename(columns={"gasoducto": "serie"})
    _lineas_max(ax, largo, "Calidad de ingreso por gasoducto — PCS ponderado")


def _lam_tratamiento(fig, plantas_df):
    ax1, ax2 = fig.subplots(2, 1)
    fig.suptitle("Tratamiento", fontsize=12, fontweight="bold")

    partes = []
    for _, f in plantas_df.iterrows():
        for etiqueta, col in (("Procesado", "vol_asignado"), ("BP", "bypass")):
            v = f.get(col)
            if pd.notna(v) and abs(float(v)) > 1e-12:
                partes.append({"periodo": f["periodo"],
                               "cat": f"{f['planta']} {etiqueta}",
                               "valor": float(v)})
    _apilada(ax1, pd.DataFrame(partes, columns=["periodo", "cat", "valor"]),
             "cat", "valor", "Procesado y ByPass del pool")

    cols = {c: e for c, e in _CORTES.items() if c in plantas_df.columns}
    if cols:
        largo = (plantas_df.groupby("periodo", as_index=False)[list(cols)].sum()
                 .melt(id_vars="periodo", var_name="cat", value_name="valor"))
        largo["cat"] = largo["cat"].map(cols)
        _apilada(ax2, largo, "cat", "valor",
                 "Retenidos por compuesto", unidad="tn/d")
    else:
        ax2.set_axis_off()
        ax2.set_title("Retenidos por corte (sin datos)", fontsize=9)


def _lam_pcs_iw(fig, plantas_df, pcs_max, iw_max):
    fig.suptitle("Calidad por planta — PCS e IW, entrada vs salida",
                 fontsize=12, fontweight="bold")
    if "pcs_in" not in plantas_df.columns \
            or plantas_df["pcs_in"].notna().sum() == 0:
        fig.text(0.5, 0.5, "La serie no trae PCS/IW por planta.", ha="center")
        return
    nombres = sorted(plantas_df["planta"].unique())
    ejes = fig.subplots(len(nombres), 2, squeeze=False)
    for j, planta in enumerate(nombres):
        d = plantas_df[plantas_df["planta"] == planta]
        for i, (cin, cout, tit, mx) in enumerate(
                (("pcs_in", "pcs_out", "PCS", pcs_max),
                 ("iw_in", "iw_out", "IW", iw_max))):
            ax = ejes[j][i]
            vv = [c for c in (cin, cout) if c in d.columns]
            largo = d.melt(id_vars="periodo", value_vars=vv, var_name="serie",
                           value_name="valor").dropna(subset=["valor"])
            largo["serie"] = largo["serie"].map({cin: "Ingreso", cout: "Salida"})
            if largo.empty:
                ax.set_axis_off()
                continue
            _lineas_max(ax, largo, f"{planta} — {tit}",
                        maximos={f"{tit} MAX": mx} if i == 0 and j == 0 else
                        {"": mx})
    fig.tight_layout(rect=(0, 0, 1, 0.94))


def _lam_caudal_capacidad(fig, plantas_df):
    fig.suptitle("Caudal disponible vs capacidad de ingreso", fontsize=12,
                 fontweight="bold")
    nombres = sorted(plantas_df["planta"].unique())
    ejes = fig.subplots(len(nombres), 1, squeeze=False)[:, 0]
    for ax, planta in zip(ejes, nombres):
        d = plantas_df[plantas_df["planta"] == planta].sort_values("periodo")
        if d["vol_disponible"].notna().any():
            ax.fill_between(d["periodo"], d["vol_disponible"].fillna(0.0),
                            alpha=0.6, color="#2E86C1", label="Disponible")
        if "capacidad_ingreso" in d.columns and d["capacidad_ingreso"].notna().any():
            ax.plot(d["periodo"], d["capacidad_ingreso"], color="#E67E22",
                    linewidth=2.2, label="Capacidad")
        ax.set_title(str(planta), fontsize=10, fontweight="bold")
        ax.set_ylabel("MMm3/d", fontsize=8)
        ax.legend(fontsize=7, framealpha=0.6)
        _fmt_eje_fechas(ax)
    fig.tight_layout(rect=(0, 0, 1, 0.93))


def _lam_resumen_anual(fig, plantas_df):
    fig.suptitle("Resumen anual por planta (promedio de los meses calculados)",
                 fontsize=12, fontweight="bold")
    d = plantas_df.copy()
    d["año"] = pd.to_datetime(d["periodo"]).dt.year
    disponibles = {fila: (col, fmt) for fila, (col, fmt) in _FILAS_RESUMEN.items()
                   if col in d.columns}
    if not disponibles:
        fig.text(0.5, 0.5, "Sin métricas para el resumen.", ha="center")
        return
    agg = d.groupby(["planta", "año"]).agg(
        **{fila: (col, "mean") for fila, (col, _) in disponibles.items()}).T
    columnas = [f"{p} {a}" for p, a in agg.columns]
    celdas = [[("—" if pd.isna(v) else fmt.format(v)) for v in agg.loc[fila]]
              for fila, (_, fmt) in disponibles.items()]

    ax = fig.subplots()
    ax.set_axis_off()
    tabla = ax.table(cellText=celdas, rowLabels=list(disponibles),
                     colLabels=columnas, loc="center", cellLoc="right")
    tabla.auto_set_font_size(False)
    tabla.set_fontsize(6.5)
    tabla.scale(1.0, 1.4)


# ===========================================================================
# Entrada principal
# ===========================================================================

def generar_reporte_pdf(serie: dict, pcs_max: float = 10_700.0,
                        iw_max: float = 13_000.0,
                        unidad_label: str = "MMm³/d STD") -> bytes:
    """Arma el PDF completo a partir del dict `serie` de ejecutar_serie.

    Devuelve los bytes del PDF, listos para `st.download_button`. Las láminas
    sin datos salen con su aviso en lugar de abortar el reporte.
    """
    plantas_df = serie.get("plantas")
    if plantas_df is None or not len(plantas_df):
        raise ValueError("La serie no tiene datos de plantas: correr primero "
                         "la serie temporal desde la barra lateral.")

    plantas_df = plantas_df.copy()
    plantas_df["periodo"] = pd.to_datetime(plantas_df["periodo"])
    areas = serie.get("areas", pd.DataFrame()).copy()
    mezcla = serie.get("mezcla", pd.DataFrame()).copy()
    for df in (areas, mezcla):
        if "periodo" in df.columns:
            df["periodo"] = pd.to_datetime(df["periodo"])

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        _pagina_titulo(pdf, lambda f: _lam_portada(f, plantas_df, mezcla,
                                                   unidad_label))
        if len(mezcla):
            _pagina_titulo(pdf, lambda f: _lam_transporte(f, mezcla, pcs_max, iw_max))
        if len(areas):
            _pagina_titulo(pdf, lambda f: _lam_produccion(f, areas))
            _pagina_titulo(pdf, lambda f: _lam_gasoductos(f, areas))
            _pagina_titulo(pdf, lambda f: _lam_calidad_gasoducto(f, areas))
        _pagina_titulo(pdf, lambda f: _lam_tratamiento(f, plantas_df))
        _pagina_titulo(pdf, lambda f: _lam_pcs_iw(f, plantas_df, pcs_max, iw_max))
        _pagina_titulo(pdf, lambda f: _lam_caudal_capacidad(f, plantas_df))
        _pagina_titulo(pdf, lambda f: _lam_resumen_anual(f, plantas_df))

    return buf.getvalue()


# ===========================================================================
# Exportación de gráficos sueltos (para presentaciones)
# ===========================================================================
#
# A diferencia de las láminas del PDF (páginas compuestas), acá cada entrada
# del catálogo es UN gráfico en UNA figura, pensado para pegarse en una slide.
# El catálogo se arma dinámicamente según lo que traiga la serie: un detalle
# por cada gasoducto grande, un PCS/IW por cada planta, etc.

import zipfile
import re as _re
import unicodedata as _ud


def _slug(texto) -> str:
    """Nombre de archivo legible: sin tildes ni simbolos, guiones bajos."""
    plano = _ud.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return _re.sub(r"[^a-z0-9]+", "_", plano.lower()).strip("_")


def _g_tpe(fig, mezcla, **_):
    ax = fig.subplots()
    etiquetas = {"vol_mega": "MEGA", "vol_tty": "TTY",
                 "vol_directo_a_gasoducto": "Directo a gasoducto"}
    cols = [c for c in etiquetas if c in mezcla.columns]
    m = mezcla.sort_values("periodo")
    datos = m[cols].fillna(0.0)
    ax.stackplot(m["periodo"], [datos[c].values for c in cols],
                 labels=[etiquetas[c] for c in cols],
                 colors=[_COLORES_TPE[etiquetas[c]] for c in cols], alpha=0.9)
    ax.legend(fontsize=8, loc="upper left", framealpha=0.6)
    ax.set_title("Inyección a sistema de tpe [MMm3/d STD]", fontsize=11,
                 fontweight="bold")
    ax.set_ylabel("MMm3/d", fontsize=9)
    _fmt_eje_fechas(ax)


def _g_calidad_mezcla(fig, mezcla, pcs_max, iw_max, **_):
    ax = fig.subplots()
    m = mezcla.sort_values("periodo")
    largo = m.melt(id_vars="periodo",
                   value_vars=[c for c in ("pcs", "iw") if c in m.columns],
                   var_name="serie", value_name="valor").dropna(subset=["valor"])
    largo["serie"] = largo["serie"].map({"pcs": "PCS", "iw": "IW"})
    _lineas_max(ax, largo, "Calidad del gas de la mezcla [kcal/m3]",
                maximos={"PCS MAX": pcs_max, "IW MAX": iw_max})


def _g_apilada_origen(fig, areas, origen, titulo, **_):
    ax = fig.subplots()
    df = areas[(areas["origen"] == origen) & areas["volumen"].notna()]
    _apilada(ax, _top_n_mas_otros(df, "area", "volumen", 10),
             "area", "volumen", titulo)


def _g_detalle_gasoducto(fig, areas, gasoducto, **_):
    ax = fig.subplots()
    df = areas[(areas["gasoducto"] == gasoducto) & areas["volumen"].notna()]
    _apilada(ax, _top_n_mas_otros(df, "area", "volumen", 10),
             "area", "volumen", f"Inyección a {gasoducto} por área")


def _g_calidad_gd(fig, areas, **_):
    ax = fig.subplots()
    d = areas[areas["gasoducto"].notna() & areas["volumen"].notna()
              & areas["pcs"].notna()].copy()
    d["pcs_x_vol"] = d["pcs"] * d["volumen"]
    pond = d.groupby(["periodo", "gasoducto"], as_index=False).agg(
        pcs_x_vol=("pcs_x_vol", "sum"), volumen=("volumen", "sum"))
    pond = pond[pond["volumen"] > 0]
    pond["valor"] = pond["pcs_x_vol"] / pond["volumen"]
    top = (pond.groupby("gasoducto")["volumen"].sum()
               .sort_values(ascending=False).head(6).index)
    _lineas_max(ax, pond[pond["gasoducto"].isin(top)]
                .rename(columns={"gasoducto": "serie"}),
                "Calidad de ingreso por gasoducto — PCS ponderado")


def _g_procesado_bp(fig, plantas_df, **_):
    ax = fig.subplots()
    partes = [{"periodo": f["periodo"], "cat": f"{f['planta']} {e}",
               "valor": float(f[c])}
              for _, f in plantas_df.iterrows()
              for e, c in (("Procesado", "vol_asignado"), ("BP", "bypass"))
              if pd.notna(f.get(c)) and abs(float(f[c])) > 1e-12]
    _apilada(ax, pd.DataFrame(partes, columns=["periodo", "cat", "valor"]),
             "cat", "valor", "Procesado y ByPass del pool")


def _g_retenidos(fig, plantas_df, **_):
    ax = fig.subplots()
    cols = {c: e for c, e in _CORTES.items() if c in plantas_df.columns}
    largo = (plantas_df.groupby("periodo", as_index=False)[list(cols)].sum()
             .melt(id_vars="periodo", var_name="cat", value_name="valor"))
    largo["cat"] = largo["cat"].map(cols)
    _apilada(ax, largo, "cat", "valor", "Retenidos por compuesto", unidad="tn/d")


def _g_pcs_iw_planta(fig, plantas_df, planta, medida, pcs_max, iw_max, **_):
    ax = fig.subplots()
    cin, cout = (f"{medida}_in", f"{medida}_out")
    d = plantas_df[plantas_df["planta"] == planta]
    largo = d.melt(id_vars="periodo",
                   value_vars=[c for c in (cin, cout) if c in d.columns],
                   var_name="serie", value_name="valor").dropna(subset=["valor"])
    largo["serie"] = largo["serie"].map({cin: "Ingreso", cout: "Salida"})
    mx = pcs_max if medida == "pcs" else iw_max
    _lineas_max(ax, largo, f"{planta} — {medida.upper()} entrada vs salida",
                maximos={f"{medida.upper()} MAX": mx})


def _g_caudal_cap(fig, plantas_df, planta, **_):
    ax = fig.subplots()
    d = plantas_df[plantas_df["planta"] == planta].sort_values("periodo")
    if d["vol_disponible"].notna().any():
        ax.fill_between(d["periodo"], d["vol_disponible"].fillna(0.0),
                        alpha=0.6, color="#2E86C1", label="Disponible")
    if "capacidad_ingreso" in d.columns and d["capacidad_ingreso"].notna().any():
        ax.plot(d["periodo"], d["capacidad_ingreso"], color="#E67E22",
                linewidth=2.2, label="Capacidad de ingreso")
    ax.set_title(f"{planta} — caudal vs capacidad", fontsize=11, fontweight="bold")
    ax.set_ylabel("MMm3/d", fontsize=9)
    ax.legend(fontsize=8, framealpha=0.6)
    _fmt_eje_fechas(ax)


def catalogo_graficos(serie: dict) -> dict:
    """{nombre legible -> builder(fig)} de los gráficos exportables.

    El catálogo depende de la serie: si no hay datos para un gráfico, no
    aparece como opción (mejor que ofrecerlo y exportar un cartel vacío).
    """
    plantas_df = serie.get("plantas", pd.DataFrame()).copy()
    areas = serie.get("areas", pd.DataFrame()).copy()
    mezcla = serie.get("mezcla", pd.DataFrame()).copy()
    for df in (plantas_df, areas, mezcla):
        if "periodo" in df.columns:
            df["periodo"] = pd.to_datetime(df["periodo"])

    ctx = dict(plantas_df=plantas_df, areas=areas, mezcla=mezcla)
    cat = {}

    def _agregar(nombre, fn, **kw):
        cat[nombre] = lambda fig, pcs_max, iw_max: fn(
            fig, pcs_max=pcs_max, iw_max=iw_max, **ctx, **kw)

    if len(mezcla) and any(c in mezcla.columns for c in
                           ("vol_mega", "vol_tty", "vol_directo_a_gasoducto")):
        _agregar("Inyección a sistema de transporte", _g_tpe)
    if len(mezcla) and "pcs" in mezcla.columns and mezcla["pcs"].notna().any():
        _agregar("Calidad de la mezcla (PCS / IW)", _g_calidad_mezcla)

    if len(areas):
        for origen, titulo in (("yacimientos", "Inyección por área"),
                               ("detalles_hubs", "Inyección por HUB"),
                               ("hubs", "Entrega de HUBs a plantas")):
            if (areas["origen"] == origen).any():
                _agregar(titulo, _g_apilada_origen, origen=origen, titulo=titulo)

        con_gd = areas[areas["gasoducto"].notna() & areas["volumen"].notna()]
        top_gd = (con_gd.groupby("gasoducto")["volumen"].sum()
                  .sort_values(ascending=False).head(8).index)
        for gd in top_gd:
            _agregar(f"Detalle gasoducto: {gd}", _g_detalle_gasoducto, gasoducto=gd)

        if "pcs" in areas.columns and areas["pcs"].notna().any():
            _agregar("Calidad por gasoducto (PCS ponderado)", _g_calidad_gd)

    if len(plantas_df):
        _agregar("Procesado y ByPass del pool", _g_procesado_bp)
        if any(c in plantas_df.columns for c in _CORTES):
            _agregar("Retenidos por compuesto", _g_retenidos)
        for planta in sorted(plantas_df["planta"].unique()):
            if "pcs_in" in plantas_df.columns and plantas_df["pcs_in"].notna().any():
                _agregar(f"PCS entrada/salida — {planta}", _g_pcs_iw_planta,
                         planta=planta, medida="pcs")
            if "iw_in" in plantas_df.columns and plantas_df["iw_in"].notna().any():
                _agregar(f"IW entrada/salida — {planta}", _g_pcs_iw_planta,
                         planta=planta, medida="iw")
            _agregar(f"Caudal vs capacidad — {planta}", _g_caudal_cap, planta=planta)

    return cat


def exportar_graficos(serie: dict, pedidos: list[dict], formato: str = "png",
                      pcs_max: float = 10_700.0, iw_max: float = 13_000.0):
    """Renderiza los gráficos pedidos y devuelve (bytes, nombre_archivo, mime).

    pedidos : list[dict]
        Uno por gráfico: {"nombre", "ancho_cm", "alto_cm", "dpi"}. El tamaño
        es el de la figura EN LA SLIDE (cm); el DPI define la calidad del PNG
        (200 alcanza para proyectar, 300 para imprimir). En SVG el DPI no
        aplica: es vectorial y se ve perfecto a cualquier tamaño.

    Un solo gráfico -> el archivo directo; varios -> un ZIP.
    """
    formato = formato.lower()
    catalogo = catalogo_graficos(serie)

    archivos = []
    for pedido in pedidos:
        nombre = pedido["nombre"]
        builder = catalogo.get(nombre)
        if builder is None:
            continue
        fig = plt.figure(figsize=(float(pedido.get("ancho_cm", 25.4)) / 2.54,
                                  float(pedido.get("alto_cm", 14.3)) / 2.54))
        try:
            builder(fig, pcs_max, iw_max)
            buf = io.BytesIO()
            fig.savefig(buf, format=formato, dpi=float(pedido.get("dpi", 200)),
                        bbox_inches="tight", facecolor="white")
            archivos.append((f"{_slug(nombre)}.{formato}", buf.getvalue()))
        finally:
            plt.close(fig)

    if not archivos:
        raise ValueError("Ningún gráfico seleccionado se pudo generar.")

    if len(archivos) == 1:
        nombre, contenido = archivos[0]
        mime = "image/svg+xml" if formato == "svg" else "image/png"
        return contenido, nombre, mime

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, contenido in archivos:
            zf.writestr(nombre, contenido)
    return buf.getvalue(), "graficos_presentacion.zip", "application/zip"
