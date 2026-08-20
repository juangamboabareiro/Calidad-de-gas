"""
Paso de inyeccion a 9300 kcal/m3 hacia volumen estandar.

Cambio importante respecto de la version anterior
--------------------------------------------------
Antes la division era:

    pd.concat([inyeccion_9300.iloc[:, :2],
               inyeccion_9300.iloc[:, 2:] / coeficientes.iloc[:, 1:]], axis=1)

Eso divide alineando por el indice posicional (0, 1, 2...) de dos hojas
DISTINTAS del Excel. Funciona solo mientras las dos tengan exactamente las
mismas areas en exactamente el mismo orden. Si alguien inserta, borra o
reordena una fila en una sola de las dos hojas, cada area empieza a usar el
coeficiente de otra area y no hay ningun error: los numeros simplemente
quedan mal.

Aca la division se hace alineando por Area, que es lo que realmente
significa la operacion.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from domain.columnas import (
    COL_ANIO,
    COL_AREA,
    COL_CUENCA,
    COL_ESTACION,
    COL_MES,
    COL_PERIODO,
    COL_VOLUMEN,
)
from domain.normalizacion import asignar_estacion_serie


def calcular_inyeccion_std(
    inyeccion_9300: pd.DataFrame,
    coeficientes: pd.DataFrame,
    *,
    formato_periodo: str = "%m-%Y",
) -> pd.DataFrame:
    """
    Convierte la inyeccion a 9300 en volumen estandar y la pasa a formato largo.

    Parameters
    ----------
    inyeccion_9300 : pandas.DataFrame
        Hoja Inyeccion-9300. Formato ancho: Area, Cuenca y una columna por
        periodo mensual.
    coeficientes : pandas.DataFrame
        Hoja Coeficientes. Area y una columna por periodo, con los mismos
        encabezados que la hoja anterior.
    formato_periodo : str
        Formato de los encabezados de periodo.

    Returns
    -------
    pandas.DataFrame
        Columnas: Area, Cuenca, Periodo, Volumen, Anio, Mes, Estacion.
    """
    inyeccion = inyeccion_9300.copy()
    coefs = coeficientes.copy()

    inyeccion = _descartar_filas_sin_area(inyeccion, "inyeccion_9300")
    coefs = _descartar_filas_sin_area(coefs, "coeficientes")

    coefs = _resolver_duplicados(coefs, "coeficientes")

    volumen_std = _dividir_por_coeficientes(inyeccion, coefs)

    return _a_formato_largo(volumen_std, formato_periodo)


def _resolver_duplicados(df: pd.DataFrame, nombre: str) -> pd.DataFrame:
    """
    Deja una sola fila por Area, o corta si eso implicaria elegir a ciegas.

    Por que importa: la division alinea por Area. Si un area aparece dos veces,
    pandas no sabe cual de las dos filas de coeficientes usar y, segun el caso,
    levanta error o arma un producto cartesiano que duplica volumenes.

    Se distinguen dos situaciones:

    - Filas identicas en todas sus columnas: es un duplicado de carga y se
      puede descartar sin perder informacion.
    - Filas con la misma Area pero valores distintos: hay algo que las
      diferencia que la clave Area no captura (cuenca, operador, un split del
      area). Elegir una seria inventar un dato, asi que se corta.

    Raises
    ------
    ValueError
        Si quedan areas repetidas con valores distintos.
    """
    if COL_AREA not in df.columns or not df[COL_AREA].duplicated().any():
        return df

    identicas = df.duplicated(keep="first")

    if identicas.any():
        print(f"[{nombre}] {identicas.sum()} filas duplicadas exactas, se descartan")
        df = df[~identicas]

    repetidas = df[COL_AREA].duplicated(keep=False)

    if repetidas.any():
        areas = sorted(df.loc[repetidas, COL_AREA].unique())
        raise ValueError(
            f"[{nombre}] areas repetidas con valores distintos: {areas}. "
            "Hay que resolverlo en el Excel de inputs: o se unifican las filas, "
            "o falta una columna que distinga los casos (cuenca, operador...). "
            "Para inspeccionarlas:\n"
            f"    coeficientes[coeficientes['{COL_AREA}'].duplicated(keep=False)]"
        )

    return df


def _descartar_filas_sin_area(df: pd.DataFrame, nombre: str) -> pd.DataFrame:
    """
    Saca las filas cuya Area es nula o vacia.

    Motivo: el `.fillna(0)` de la version anterior se aplicaba al DataFrame
    ENTERO, columnas de texto incluidas. Una fila con el area vacia terminaba
    con Area = 0 (el numero), que al normalizarse quedaba como el area "0" y
    viajaba por todo el pipeline como un yacimiento fantasma.
    """
    if COL_AREA not in df.columns:
        return df

    validas = df[COL_AREA].notna() & (df[COL_AREA].astype(str).str.strip() != "")

    if (~validas).any():
        print(f"[{nombre}] {(~validas).sum()} filas descartadas por Area vacia")

        numericas = df.loc[~validas].select_dtypes("number")
        if len(numericas.columns) and numericas.to_numpy().sum() != 0:
            print(
                f"[{nombre}] OJO esas filas traian volumen "
                f"(total {numericas.to_numpy().sum():,.0f}), no era basura vacia"
            )

    return df[validas]


def _dividir_por_coeficientes(
    inyeccion: pd.DataFrame,
    coefs: pd.DataFrame,
) -> pd.DataFrame:
    """
    Divide periodo a periodo alineando por Area.

    Returns
    -------
    pandas.DataFrame
        Indexado por (Area, Cuenca), una columna por periodo.
    """
    inyeccion = inyeccion.set_index([COL_AREA, COL_CUENCA])
    coefs = coefs.set_index(COL_AREA)

    faltan_periodos = set(inyeccion.columns) - set(coefs.columns)
    if faltan_periodos:
        print(
            f"[inyeccion_std] {len(faltan_periodos)} periodos sin coeficiente: "
            f"{sorted(faltan_periodos)[:5]}"
        )

    areas = inyeccion.index.get_level_values(COL_AREA)
    faltan_areas = set(areas) - set(coefs.index)
    if faltan_areas:
        print(
            f"[inyeccion_std] {len(faltan_areas)} areas sin coeficiente: "
            f"{sorted(faltan_areas)[:5]}"
        )

    # reindex trae el coeficiente de cada area en el orden de la tabla de
    # inyeccion; las areas sin coeficiente quedan en NaN.
    coefs_alineados = coefs.reindex(areas)
    coefs_alineados.index = inyeccion.index

    volumen_std = inyeccion.div(coefs_alineados)

    # Coeficiente 0 produce inf. Se trata como dato faltante, no como volumen.
    return volumen_std.replace([np.inf, -np.inf], np.nan)


def _a_formato_largo(volumen_std: pd.DataFrame, formato_periodo: str) -> pd.DataFrame:
    """Pasa a un renglon por (Area, Cuenca, Periodo) y agrega Anio/Mes/Estacion."""
    largo = (
        volumen_std.reset_index()
        .melt(
            id_vars=[COL_AREA, COL_CUENCA],
            var_name=COL_PERIODO,
            value_name=COL_VOLUMEN,
        )
    )

    sin_volumen = largo[COL_VOLUMEN].isna().sum()
    if sin_volumen:
        print(f"[inyeccion_std] {sin_volumen} celdas sin volumen, se rellenan con 0")

    largo[COL_VOLUMEN] = largo[COL_VOLUMEN].fillna(0)

    largo[COL_PERIODO] = pd.to_datetime(largo[COL_PERIODO], format=formato_periodo)
    largo[COL_ANIO] = largo[COL_PERIODO].dt.year
    largo[COL_MES] = largo[COL_PERIODO].dt.month
    largo[COL_ESTACION] = asignar_estacion_serie(largo[COL_MES])

    return largo
