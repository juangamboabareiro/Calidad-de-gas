"""
Flujos directos de area a gasoducto (hoja Flujos-Directos).

Cambio importante respecto de la version anterior
--------------------------------------------------
La version vieja hacia un merge contra `matriz_inyecciones` y despues
descartaba TODAS las columnas que ese merge aportaba (se quedaba con Area_y
y Gasoducto_y, ambas provenientes de flujos_directos). Es decir: el merge no
agregaba informacion; a lo sumo duplicaba filas cuando una clave matcheaba
mas de una vez.

Aca se elimina ese merge. Antes de dar por buena la eliminacion, corre
`diagnosticar_merge_matriz` una vez sobre tus datos reales: si te confirma
que no habia matches, el merge era codigo muerto y no hay nada mas que hacer.
Si te dice que SI habia matches, avisame porque entonces habia duplicacion de
volumenes en los resultados viejos.
"""

from __future__ import annotations

import pandas as pd

from domain.columnas import COL_AREA, COL_GASODUCTO, COL_INYECCION, COL_VOLUMEN
from pipeline.comunes import melt_gasoductos


def calcular_inyeccion_flujos_directos(
    flujos_directos: pd.DataFrame,
    *,
    descartar_ceros: bool = True,
) -> pd.DataFrame:
    """
    Pasa la hoja de flujos directos a formato largo.

    Parameters
    ----------
    flujos_directos : pandas.DataFrame
        Hoja Flujos-Directos preprocesada (Area normalizada en
        `preprocesamiento`, no aca). Formato ancho: una columna por gasoducto.
    descartar_ceros : bool
        Si es True, saca los pares (Area, Gasoducto) sin volumen.

    Returns
    -------
    pandas.DataFrame
        Columnas: Area, Inyección, Gasoducto, Volumen.
    """
    largo = melt_gasoductos(
        flujos_directos.copy(),
        id_vars=[COL_AREA, COL_INYECCION],
    )

    if descartar_ceros:
        # `.fillna(0)` primero porque NaN != 0 da True y los nulos se colaban
        # por el filtro en la version anterior.
        largo = largo[largo[COL_VOLUMEN].fillna(0).ne(0)]

    return largo.reset_index(drop=True)


def diagnosticar_merge_matriz(
    flujos_directos: pd.DataFrame,
    matriz_inyecciones: pd.DataFrame,
) -> pd.DataFrame:
    """
    Verifica si el merge eliminado aportaba algo.

    Reproduce el cruce de la version vieja (matriz_inyecciones.Area contra
    flujos_directos.Gasoducto) y reporta cuantas filas matcheaban.

    Correr una sola vez, a mano, para validar. No es parte del pipeline.

    Returns
    -------
    pandas.DataFrame
        Los pares que matcheaban (vacio == el merge era inocuo).
    """
    largo = melt_gasoductos(
        flujos_directos.copy(),
        id_vars=[COL_AREA, COL_INYECCION],
    )

    gasoductos = set(largo[COL_GASODUCTO].dropna().unique())
    areas_matriz = set(matriz_inyecciones[COL_AREA].dropna().unique())

    interseccion = gasoductos & areas_matriz

    print(f"Gasoductos en flujos_directos: {len(gasoductos)}")
    print(f"Areas en matriz_inyecciones:   {len(areas_matriz)}")
    print(f"Coincidencias:                 {len(interseccion)}")

    if not interseccion:
        print("-> El merge viejo no matcheaba nada. Era codigo muerto.")
    else:
        print(f"-> OJO, si habia matches: {sorted(interseccion)[:10]}")
        conteo = (
            matriz_inyecciones[matriz_inyecciones[COL_AREA].isin(interseccion)]
            .groupby(COL_AREA)
            .size()
        )
        multiples = conteo[conteo > 1]
        if len(multiples):
            print(f"-> Y {len(multiples)} de esas claves duplicaban filas:")
            print(multiples)

    return matriz_inyecciones[matriz_inyecciones[COL_AREA].isin(interseccion)]
