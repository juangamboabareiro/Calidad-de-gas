"""
Operaciones que se repiten en yacimientos, detalles_hubs y flujos_directos.

Antes cada archivo tenia su propia copia de "mergear con plantas_yacimientos
y rellenar HUB". Cuando la regla cambie, ahora se cambia en un solo lugar.
"""

from __future__ import annotations

import pandas as pd

from domain.checks import merge_validado
from domain.columnas import COL_AREA, COL_GASODUCTO, COL_HUB, COL_VOLUMEN, HUB_DEFAULT


def agregar_hub(
    df: pd.DataFrame,
    plantas_yacimientos: pd.DataFrame,
    *,
    nombre: str = "agregar_hub",
    reportar: bool | None = None,
) -> pd.DataFrame:
    """
    Agrega la columna HUB cruzando por Area.

    Las areas que no figuran en `plantas_yacimientos` quedan como HUB_DEFAULT.

    Parameters
    ----------
    df : pandas.DataFrame
        Tabla con columna Area ya normalizada.
    plantas_yacimientos : pandas.DataFrame
        Diccionario Area -> HUB. Debe tener una fila por area.
    nombre : str
        Etiqueta para los mensajes de diagnostico.
    reportar : bool | None
        Poner en False cuando las filas sin HUB son esperables y su ruido
        tapa a los merges donde un sin-match si seria un error.

    Returns
    -------
    pandas.DataFrame
        Copia de `df` con la columna HUB.
    """
    salida = merge_validado(
        df,
        plantas_yacimientos,
        nombre=nombre,
        on=COL_AREA,
        how="left",
        validate="m:1",          # una sola fila por area del lado derecho
        col_ejemplo=COL_AREA,
        reportar=reportar,
    )

    salida[COL_HUB] = salida[COL_HUB].fillna(HUB_DEFAULT)

    return salida


def columnas_gasoductos(df: pd.DataFrame, id_vars: list[str]) -> list[str]:
    """
    Devuelve las columnas que representan gasoductos.

    Son "todo lo que no es identificador". Se calcula explicitamente para que
    un melt no se trague columnas que se agregaron despues (HUB, Cuenca...).

    Parameters
    ----------
    df : pandas.DataFrame
    id_vars : list[str]
        Columnas identificadoras (Area, Inyeccion, etc.).

    Returns
    -------
    list[str]
    """
    return [c for c in df.columns if c not in id_vars]


def melt_gasoductos(
    df: pd.DataFrame,
    id_vars: list[str],
    *,
    value_vars: list[str] | None = None,
) -> pd.DataFrame:
    """
    Pasa la tabla de formato ancho (una columna por gasoducto) a formato largo.

    A diferencia de un `melt` pelado, exige/calcula `value_vars` para no
    arrastrar columnas descriptivas hacia la columna Gasoducto.

    Parameters
    ----------
    df : pandas.DataFrame
    id_vars : list[str]
        Columnas que se mantienen como identificadores.
    value_vars : list[str] | None
        Columnas de gasoducto. Si es None se infieren con `columnas_gasoductos`.

    Returns
    -------
    pandas.DataFrame
        Columnas: id_vars + [Gasoducto, Volumen].
    """
    if value_vars is None:
        value_vars = columnas_gasoductos(df, id_vars)

    largo = df.melt(
        id_vars=id_vars,
        value_vars=value_vars,
        var_name=COL_GASODUCTO,
        value_name=COL_VOLUMEN,
    )

    # Si alguna columna de gasoducto venia con texto, el melt deja la columna
    # como object y las cuentas posteriores fallan en silencio.
    largo[COL_VOLUMEN] = pd.to_numeric(largo[COL_VOLUMEN], errors="coerce")

    return largo
