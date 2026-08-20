"""
Inyeccion primaria (hoja Yacimientos).

Flujo:
    yacimientos            -> [+ HUB]        -> yacimientos_areas
    yacimientos_areas      -> [formato largo] -> un renglon por (Area, Gasoducto)
    inyeccion_area         -> [+ volumen]    -> inyeccion_yacimientos_areas
"""

from __future__ import annotations

import pandas as pd

from domain.checks import merge_validado
from domain.columnas import (
    COL_AREA,
    COL_GASODUCTO,
    COL_INYECCION,
    COL_VOLUMEN,
    INYECCION_DEFAULT,
)
from pipeline.comunes import agregar_hub, melt_gasoductos


def calcular_yacimientos_areas(
    yacimientos: pd.DataFrame,
    plantas_yacimientos: pd.DataFrame,
) -> pd.DataFrame:
    """
    Agrega el HUB a la tabla de inyeccion primaria.

    Parameters
    ----------
    yacimientos : pandas.DataFrame
        Hoja Yacimientos ya preprocesada (Area normalizada).
    plantas_yacimientos : pandas.DataFrame
        Diccionario Area -> HUB.

    Returns
    -------
    pandas.DataFrame
    """
    return agregar_hub(
        yacimientos.copy(),
        plantas_yacimientos,
        nombre="yacimientos_areas",
    )


def calcular_inyeccion_yacimientos_areas(
    *,
    yacimientos: pd.DataFrame,
    plantas_yacimientos: pd.DataFrame,
    inyeccion_area: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Cruza la matriz Area x Gasoducto con el volumen inyectado por yacimiento.

    Parameters
    ----------
    yacimientos : pandas.DataFrame
        Hoja Yacimientos preprocesada.
    plantas_yacimientos : pandas.DataFrame
        Diccionario Area -> HUB.
    inyeccion_area : pandas.DataFrame
        Salida de `calcular_inyeccion_area`: un renglon por (Area, Gasoducto).

    Returns
    -------
    yacimientos_areas : pandas.DataFrame
        Tabla intermedia, en formato ancho, con HUB.
    inyeccion_yacimientos_areas : pandas.DataFrame
        Tabla final con la columna Volumen por destino.

    Notes
    -----
    El volumen ausente se rellena con 0 asumiendo "esa area no inyecta a ese
    gasoducto". Si el area falta por un problema de normalizacion, el 0 tapa
    el error: por eso `merge_validado` reporta cuantas filas no matchearon.
    """
    yacimientos_areas = calcular_yacimientos_areas(yacimientos, plantas_yacimientos)

    # `id_vars` explicito: si no, HUB (agregado recien arriba) terminaria
    # convertido en un "gasoducto" con volumen de tipo texto.
    yacimientos_largo = melt_gasoductos(
        yacimientos_areas,
        id_vars=[COL_AREA, COL_INYECCION],
    )

    inyeccion_yacimientos_areas = merge_validado(
        inyeccion_area,
        yacimientos_largo,
        nombre="inyeccion_yacimientos_areas",
        on=[COL_AREA, COL_GASODUCTO],
        how="left",
        validate="m:1",   # una fila por (Area, Gasoducto) del lado derecho
        col_ejemplo=COL_AREA,
        reportar=False,   # el reporte crudo mezcla dos casos: ver abajo
    )

    _reportar_sin_inyeccion(inyeccion_yacimientos_areas, yacimientos_largo)

    # La hoja Yacimientos ES la inyeccion primaria por definicion: lo que no
    # trae etiqueta se rotula asi, no es un dato faltante.
    inyeccion_yacimientos_areas[COL_INYECCION] = (
        inyeccion_yacimientos_areas[COL_INYECCION].fillna(INYECCION_DEFAULT)
    )

    inyeccion_yacimientos_areas[COL_VOLUMEN] = (
        inyeccion_yacimientos_areas[COL_VOLUMEN].fillna(0)
    )

    return yacimientos_areas, inyeccion_yacimientos_areas


def _reportar_sin_inyeccion(
    inyeccion_yacimientos_areas: pd.DataFrame,
    yacimientos_largo: pd.DataFrame,
) -> None:
    """
    Distingue los dos motivos por los que una fila puede quedar sin volumen.

    Caso esperable
        El area no figura en la hoja Yacimientos porque no tiene inyeccion
        primaria (Acambuco, Aguarague, etc.). Volumen 0 es el dato correcto
        y la fila se conserva para que el area siga apareciendo en la tabla.

    Caso sospechoso
        El area SI esta en la hoja Yacimientos, pero no para ese gasoducto.
        Puede ser legitimo, o puede ser un gasoducto mal escrito / un dato
        que falta. Ahi el fillna(0) borraria volumen real, asi que se avisa.

    Llamar ANTES del fillna, mientras los faltantes todavia son NaN.
    """
    areas_con_inyeccion = set(yacimientos_largo[COL_AREA].dropna())

    faltantes = inyeccion_yacimientos_areas[COL_VOLUMEN].isna()

    if not faltantes.any():
        return

    filas = inyeccion_yacimientos_areas.loc[faltantes]

    esperables = filas[~filas[COL_AREA].isin(areas_con_inyeccion)]
    sospechosas = filas[filas[COL_AREA].isin(areas_con_inyeccion)]

    print(
        f"[yacimientos] {filas[COL_AREA].nunique()} areas sin volumen: "
        f"{esperables[COL_AREA].nunique()} sin inyeccion primaria (ok), "
        f"{sospechosas[COL_AREA].nunique()} con inyeccion pero sin ese gasoducto"
    )

    if len(sospechosas):
        print("[yacimientos] revisar pares (Area, Gasoducto):")
        print(sospechosas[[COL_AREA, COL_GASODUCTO]].drop_duplicates().head(15))
