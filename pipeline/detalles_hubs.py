"""
Detalle de HUBs (hoja Detalles-HUBs).

A diferencia de yacimientos y flujos_directos, aca no se calcula ninguna
inyeccion: la tabla ya viene con los volumenes y lo unico que se hace es
etiquetar cada area con su HUB. El nombre de la funcion lo refleja.
"""

from __future__ import annotations

import pandas as pd

from pipeline.comunes import agregar_hub


def calcular_detalles_hubs_areas(
    detalles_hubs: pd.DataFrame,
    plantas_yacimientos: pd.DataFrame,
) -> pd.DataFrame:
    """
    Agrega la columna HUB a la tabla de detalles.

    Parameters
    ----------
    detalles_hubs : pandas.DataFrame
        Hoja Detalles-HUBs ya preprocesada (Area normalizada en
        `preprocesamiento`, no aca).
    plantas_yacimientos : pandas.DataFrame
        Diccionario Area -> HUB.

    Returns
    -------
    pandas.DataFrame
        Copia de la entrada con la columna HUB. Las areas sin HUB conocido
        quedan como HUB_DEFAULT ("Otros").
    """
    return agregar_hub(
        detalles_hubs.copy(),
        plantas_yacimientos,
        nombre="detalles_hubs_areas",
    )


# Alias temporal para no romper imports viejos mientras migras main.py.
# Borralo cuando termines de actualizar las llamadas.
calcular_inyeccion_detalles_hubs = calcular_detalles_hubs_areas
