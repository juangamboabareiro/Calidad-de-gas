"""
Compatibilidad con Streamlit y con Arrow.
=========================================

Dos cosas chicas que aparecen en los logs y no deberian estar.

1. `use_container_width` esta deprecado
--------------------------------------
Streamlit 1.61 avisa en cada llamada que hay que usar `width="stretch"` o
`width="content"`, y que el parametro viejo se remueve despues del 31/12/2025 —
fecha ya vencida. Son nueve avisos por render, que tapan cualquier mensaje util
del pipeline.

No se puede hardcodear el nombre nuevo porque rompe con Streamlit viejo, asi que
`ancho()` devuelve el kwarg que corresponda a la version instalada y se usa como
`st.dataframe(df, **ancho())`.

2. Arrow no traga columnas de tipo mezclado
-------------------------------------------
    ArrowInvalid: Could not convert 'VMN' with type str: tried to convert to
    int64 — Conversion failed for column Gasoducto

Pasa porque el pipeline hace `fillna(0)` sobre las tablas: la columna
`Gasoducto` termina con enteros (los ceros) Y strings ('VMN'). pyarrow infiere
int64 de los primeros valores y explota con el primer string.

Streamlit lo arregla solo y muestra la tabla igual, pero deja un traceback
completo en el log por cada render. `arrow_safe` normaliza a texto las columnas
`object` que tienen tipos mezclados, ANTES de mostrarlas. No toca las numericas:
una columna de floats sigue siendo de floats y se sigue pudiendo formatear.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def _version() -> tuple[int, int]:
    try:
        partes = str(st.__version__).split(".")
        return int(partes[0]), int(partes[1])
    except Exception:
        # Sin version legible se asume moderna: el kwarg viejo ya esta vencido.
        return (99, 99)


# El renombre entro en 1.49. Se resuelve UNA vez al importar y no por llamada.
_USA_WIDTH = _version() >= (1, 49)


def ancho(estirar: bool = True) -> dict:
    """Kwargs de ancho para `st.dataframe`, `st.button`, etc.

    Se usa como `st.dataframe(df, **ancho())` en lugar de
    `use_container_width=True`.
    """
    if _USA_WIDTH:
        return {"width": "stretch" if estirar else "content"}
    return {"use_container_width": estirar}


def arrow_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Copia lista para `st.dataframe`, sin columnas de tipo mezclado.

    Solo toca las columnas `object` donde conviven mas de un tipo de Python.
    Una columna de puros strings se deja como esta, y una numerica tambien: si
    se convirtiera todo a texto se perderia el formato de los volumenes.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df

    salida = df.copy()

    for columna in salida.columns:
        if salida[columna].dtype != object:
            continue

        valores = salida[columna].dropna()
        if valores.empty:
            continue

        # `head(200)` alcanza: si los primeros doscientos son homogeneos, el
        # riesgo de que pyarrow falle mas adelante es despreciable, y recorrer
        # tablas de miles de filas en cada render no sale gratis.
        tipos = {type(v) for v in valores.head(200)}
        if len(tipos) > 1:
            salida[columna] = salida[columna].astype(str)

    return salida


def dataframe(df, **kwargs):
    """`st.dataframe` con las dos correcciones aplicadas.

    Acepta un DataFrame o un Styler. Con Styler no se puede normalizar (habria
    que rearmar el formato), asi que solo se corrige el ancho: el que quiera la
    normalizacion tiene que llamar a `arrow_safe` antes de armar el Styler.
    """
    kwargs.setdefault("width", ancho()["width"] if _USA_WIDTH else None)
    if not _USA_WIDTH:
        kwargs.pop("width", None)
        kwargs.setdefault("use_container_width", True)

    if isinstance(df, pd.DataFrame):
        df = arrow_safe(df)

    return st.dataframe(df, **kwargs)
