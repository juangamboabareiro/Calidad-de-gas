"""
Promedio anual de inyeccion y asignacion de destinos.

Flujo:
    inyeccion_std  -> [promedio por anio] -> inyeccion
    inyeccion      -> [+ HUB]             -> inyeccion
    inyeccion      -> [+ destinos]        -> inyeccion_area
"""

from __future__ import annotations

import pandas as pd

from domain.checks import merge_validado
from domain.columnas import COL_ANIO, COL_AREA, COL_CUENCA, COL_GASODUCTO, COL_VOLUMEN
from pipeline.comunes import agregar_hub


def calcular_inyeccion(
    inyeccion_std: pd.DataFrame,
    plantas_yacimientos: pd.DataFrame,
) -> pd.DataFrame:
    """
    Promedia el volumen mensual por anio y agrega el HUB.

    Parameters
    ----------
    inyeccion_std : pandas.DataFrame
        Serie mensual en formato largo.
    plantas_yacimientos : pandas.DataFrame
        Diccionario Area -> HUB.

    Returns
    -------
    pandas.DataFrame
        Una fila por (Area, Cuenca), una columna por anio, mas HUB.
    """
    promedio_anual = (
        inyeccion_std
        .groupby([COL_ANIO, COL_AREA, COL_CUENCA])[COL_VOLUMEN]
        .mean()
        .unstack(COL_ANIO)
        .reset_index()   # explicito: Area y Cuenca como columnas, no como indice
    )

    return agregar_hub(promedio_anual, plantas_yacimientos, nombre="inyeccion")


def calcular_inyeccion_area(
    inyeccion: pd.DataFrame,
    matriz_inyecciones: pd.DataFrame,
    *,
    reportar_sin_destino: bool = True,
) -> pd.DataFrame:
    """
    Asigna a cada area sus destinos segun la matriz de inyecciones.

    Parameters
    ----------
    inyeccion : pandas.DataFrame
        Salida de `calcular_inyeccion`.
    matriz_inyecciones : pandas.DataFrame
        Matriz origen-destino en formato largo (Gasoducto, Area).
    reportar_sin_destino : bool
        Avisa cuales areas quedaron sin ningun destino asignado.

    Returns
    -------
    pandas.DataFrame
        Una fila por (Area, Gasoducto). Las areas que no figuran en la matriz
        aparecen una sola vez, con Gasoducto en NaN.

    Notes
    -----
    El merge es 1:m a proposito: un area puede inyectar a varios gasoductos y
    tiene que generar una fila por cada uno.
    """
    inyeccion_area = merge_validado(
        inyeccion,
        matriz_inyecciones,
        nombre="inyeccion_area",
        on=COL_AREA,
        how="left",
        validate="1:m",
        col_ejemplo=COL_AREA,
        reportar=False,   # el reporte util es el de abajo, mas especifico
    )

    if reportar_sin_destino:
        _reportar_areas_sin_destino(inyeccion_area)

    return inyeccion_area


def _reportar_areas_sin_destino(inyeccion_area: pd.DataFrame) -> None:
    """
    Avisa que areas no tienen ningun destino en la matriz de inyecciones.

    Por que importa: un area con volumen y sin destino es gas que entra al
    modelo y no llega a ninguna tabla total. No es algo que el codigo pueda
    arreglar (falta una fila en la hoja Matriz-Inyecciones), pero quien carga
    ese input es justamente quien mira estos mensajes.

    Se reporta el volumen del ultimo anio disponible para dar una idea de la
    magnitud: no es lo mismo un area agotada que una en produccion.
    """
    sin_destino = inyeccion_area[inyeccion_area[COL_GASODUCTO].isna()]

    if sin_destino.empty:
        return

    areas = sorted(sin_destino[COL_AREA].dropna().unique())

    # Las columnas de anio son las unicas numericas de la tabla.
    anios = [c for c in inyeccion_area.columns if isinstance(c, (int, float))]

    detalle = ""
    if anios:
        ultimo = max(anios)
        con_volumen = sin_destino[sin_destino[ultimo].fillna(0) > 0]
        detalle = (
            f", {len(con_volumen)} con volumen en {ultimo} "
            f"(total {con_volumen[ultimo].sum():,.0f})"
        )

    print(f"[inyeccion_area] OJO {len(areas)} areas sin destino en la matriz{detalle}")
    print(f"[inyeccion_area] areas: {areas[:15]}")
