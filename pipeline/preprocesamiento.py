"""
Preparacion de los inputs crudos antes del pipeline de calculo.

Que quedo y que se fue
----------------------
Se fue toda la normalizacion de Area: ahora la hacen los loaders, que es el
unico borde por donde entran los datos. Si una tabla llega aca, su clave ya
es confiable.

Queda el relleno de nulos y los cambios de forma (melt, set_index) que no son
calculo de negocio pero tampoco son lectura.

Cada tabla tiene su propia funcion. Antes era un solo bloque de 40 lineas que
devolvia 8 valores posicionales: si algun dia habia que reordenarlos, se
rompia todo en silencio.
"""

from __future__ import annotations

import pandas as pd

from config import PATH_INPUTS
from domain.columnas import (
    COL_AREA,
    COL_COEF_INYECCION,
    COL_GASODUCTO,
    COL_PERIODO,
)
from domain.ctes_gas import COMPUESTOS
from domain.normalizacion import canonizar_areas
from io_.loaders import (
    ALIAS_AREAS,
    load_coefs_inyeccion_area,
    load_matriz_inyecciones,
    load_premisas_areas,
)


def rellenar_numericos(df: pd.DataFrame, valor=0) -> pd.DataFrame:
    """
    Rellena nulos SOLO en las columnas numericas.

    La version anterior hacia `df.fillna(0)` sobre la tabla entera, asi que
    una celda de texto vacia (Area, Cuenca) quedaba con el numero 0 y se
    convertia en una categoria fantasma. Los nulos de texto tienen que seguir
    siendo nulos para que los merges los reporten.

    Parameters
    ----------
    df : pandas.DataFrame
    valor : any
        Valor de relleno.

    Returns
    -------
    pandas.DataFrame
        Copia de la entrada.
    """
    salida = df.copy()
    numericas = salida.select_dtypes("number").columns

    salida[numericas] = salida[numericas].fillna(valor)

    return salida


def preparar_matriz_inyecciones(matriz_inyecciones: pd.DataFrame) -> pd.DataFrame:
    """
    Pasa la matriz origen-destino a formato largo.

    En la hoja original cada columna es un gasoducto y sus celdas son las
    areas que inyectan ahi. Como las columnas tienen distinto largo, sobran
    celdas vacias al pie: esas filas se descartan (antes habia un
    `matriz_inyecciones.fillna('error')` que no hacia nada, porque no se
    asignaba el resultado).

    Returns
    -------
    pandas.DataFrame
        Columnas: Gasoducto, Area.
    """
    largo = matriz_inyecciones.melt(
        var_name=COL_GASODUCTO,
        value_name=COL_AREA,
    )

    largo = largo[largo[COL_AREA].notna()]

    # Aca si hay que canonizar a mano: en esta hoja las areas venian como
    # valores repartidos a lo ancho, no en una columna Area, asi que el
    # loader no pudo hacerlo.
    largo[COL_AREA] = canonizar_areas(largo[COL_AREA], ALIAS_AREAS)

    return largo[largo[COL_AREA] != ""].reset_index(drop=True)


def preparar_coefs_inyeccion_area(
    coefs_inyeccion_area: pd.DataFrame,
    *,
    formato_periodo: str = "%m-%Y",
) -> pd.DataFrame:
    """
    Pasa los coeficientes de inyeccion por area a formato largo.

    Returns
    -------
    pandas.DataFrame
        Columnas: Area, Gasoducto, Periodo, Coef_Inyeccion.
    """
    largo = coefs_inyeccion_area.melt(
        id_vars=[COL_AREA, COL_GASODUCTO],
        var_name=COL_PERIODO,
        value_name=COL_COEF_INYECCION,
    )

    largo[COL_PERIODO] = pd.to_datetime(largo[COL_PERIODO], format=formato_periodo)

    return largo


def preparar_propiedades(propiedades: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra los compuestos de interes y agrega el PCS molar.

    Returns
    -------
    pandas.DataFrame
        Indexado por Compuesto.
    """
    salida = rellenar_numericos(propiedades)

    salida = salida[salida["Compuesto"].isin(COMPUESTOS)].set_index("Compuesto")

    faltantes = set(COMPUESTOS) - set(salida.index)
    if faltantes:
        print(f"[propiedades] compuestos sin datos: {sorted(faltantes)}")

    salida["PCS [kJ/mol]"] = (
        salida["Peso molecular [kg/kmol]"] * salida["PCS [MJ/kg]"]
    )

    return salida


def preprocesar_inputs(
    *,
    flujos_directos: pd.DataFrame,
    yacimientos: pd.DataFrame,
    detalles_hubs: pd.DataFrame,
    propiedades: pd.DataFrame,
    plantas_yacimientos: pd.DataFrame,
    path_inputs=PATH_INPUTS,
) -> dict[str, pd.DataFrame]:
    """
    Deja todos los inputs listos para el pipeline.

    Parameters
    ----------
    flujos_directos, yacimientos, detalles_hubs, propiedades, plantas_yacimientos
        Salidas de los loaders (Area ya canonizada).
    path_inputs : str | pathlib.Path
        Ruta del Excel, para las hojas que se cargan aca adentro.

    Returns
    -------
    dict[str, pandas.DataFrame]
        Diccionario con las 8 tablas. Se devuelve un dict y no una tupla
        para que agregar o reordenar una tabla no rompa el desempaquetado
        en `main.py`.
    """
    matriz_inyecciones = load_matriz_inyecciones(path_inputs)
    coefs_inyeccion_area = load_coefs_inyeccion_area(path_inputs)
    premisas_areas = load_premisas_areas(path_inputs)

    return {
        "flujos_directos": rellenar_numericos(flujos_directos),
        "yacimientos": rellenar_numericos(yacimientos),
        "detalles_hubs": rellenar_numericos(detalles_hubs),
        "plantas_yacimientos": plantas_yacimientos.copy(),
        "premisas_areas": premisas_areas.copy(),
        "propiedades": preparar_propiedades(propiedades),
        "matriz_inyecciones": preparar_matriz_inyecciones(matriz_inyecciones),
        "coefs_inyeccion_area": preparar_coefs_inyeccion_area(coefs_inyeccion_area),
    }
