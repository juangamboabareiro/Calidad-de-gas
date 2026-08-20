"""
Normalizacion de texto y clasificacion de estaciones.

`normalizar` construye la clave con la que se cruzan TODAS las tablas del
proyecto, asi que cualquier cambio aca impacta en todos los merges.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import pandas as pd


# Todo lo que no sea letra ASCII o digito se elimina.
_NO_ALFANUMERICO = re.compile(r"[^a-z0-9]")


@lru_cache(maxsize=4096)
def _normalizar_str(texto: str) -> str:
    """Nucleo de la normalizacion, cacheado. Recibe siempre str."""
    texto = texto.strip().lower()

    # NFD separa la letra de su tilde ("á" -> "a" + acento combinante),
    # y despues descartamos los acentos (categoria Unicode "Mn").
    texto = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )

    return _NO_ALFANUMERICO.sub("", texto)


def normalizar(texto):
    """
    Normaliza una cadena de texto para facilitar comparaciones.

    La funcion:
    - Convierte el texto a minusculas.
    - Elimina espacios al inicio y al final.
    - Elimina tildes y otros signos diacriticos.
    - Conserva unicamente letras ASCII (a-z) y digitos (0-9).

    Parameters
    ----------
    texto : str | any
        Valor a normalizar. Si es nulo (NaN/None), se devuelve sin modificar.

    Returns
    -------
    str | any
        Texto normalizado, o el valor original si es nulo.

    Examples
    --------
    >>> normalizar("Gas Rico")
    'gasrico'
    >>> normalizar("Área_1")
    'area1'
    >>> normalizar("  Cañadón Seco  ")
    'canadonseco'
    """
    if texto is None or (not isinstance(texto, str) and pd.isna(texto)):
        return texto

    return _normalizar_str(str(texto))


def normalizar_serie(serie: pd.Series) -> pd.Series:
    """
    Version vectorizada de `normalizar` para una columna entera.

    Equivale a `serie.apply(normalizar)` pero usando el motor `.str` de pandas,
    que es bastante mas rapido en tablas grandes. Los nulos quedan como NaN.

    Parameters
    ----------
    serie : pandas.Series

    Returns
    -------
    pandas.Series
        Serie de strings normalizados (NaN donde habia nulos).
    """
    return (
        serie.astype("string")
        .str.strip()
        .str.lower()
        .str.normalize("NFD")
        .str.encode("ascii", errors="ignore")
        .str.decode("ascii")
        .str.replace(_NO_ALFANUMERICO, "", regex=True)
    )


# --------------------------------------------------------------------------
# Estaciones
# --------------------------------------------------------------------------

# PENDIENTE DE CONFIRMAR: el criterio original no clasificaba octubre.
# Hasta que se defina, cae en MES_SIN_ESTACION en vez de perderse.
MES_SIN_ESTACION = "sin_estacion"

ESTACIONES: dict[int, str] = {
    1: "verano",
    2: "verano",
    3: "verano",
    4: "verano",
    5: "invierno",
    6: "invierno",
    7: "invierno",
    8: "invierno",
    9: "invierno",
    10: MES_SIN_ESTACION,   # <-- confirmar con negocio
    11: "verano",
    12: "verano",
}


def asignar_estacion(mes, estricto: bool = False) -> str:
    """
    Clasifica un mes como verano o invierno segun el criterio operativo.

    Parameters
    ----------
    mes : int
        Numero de mes (1-12).
    estricto : bool
        Si es True, un mes fuera de 1-12 levanta ValueError en vez de
        devolver el sentinela.

    Returns
    -------
    str
        'verano', 'invierno' o MES_SIN_ESTACION.
    """
    try:
        return ESTACIONES[int(mes)]
    except (KeyError, ValueError, TypeError) as error:
        if estricto:
            raise ValueError(f"Mes invalido: {mes!r}") from error
        return MES_SIN_ESTACION


def asignar_estacion_serie(serie: pd.Series) -> pd.Series:
    """
    Version vectorizada: `serie.map(ESTACIONES)` con relleno del sentinela.

    Reemplaza a `serie.apply(asignar_estacion)`.
    """
    return serie.map(ESTACIONES).fillna(MES_SIN_ESTACION)


# --------------------------------------------------------------------------
# Canonizacion: que el codigo no dependa de como escribio el nombre la persona
# --------------------------------------------------------------------------

def canonizar_columnas(df: pd.DataFrame, canonicas) -> pd.DataFrame:
    """
    Renombra los encabezados que "son" una columna conocida escrita distinto.

    Compara el encabezado normalizado contra el nombre canonico normalizado.
    Asi "Inyeccion", "inyección" e "INYECCIÓN " terminan todos como el
    canonico "Inyección", y el codigo puede usar la constante sin depender de
    como quedo escrito en el Excel.

    Las columnas que no matchean con ninguna canonica quedan intactas: es
    importante, porque en las hojas anchas los encabezados son gasoductos y
    no hay que tocarlos.

    Parameters
    ----------
    df : pandas.DataFrame
    canonicas : iterable[str]
        Nombres tal como los espera el codigo (usar las constantes de
        `domain.columnas`).

    Returns
    -------
    pandas.DataFrame
        Copia con los encabezados corregidos.

    Examples
    --------
    >>> import pandas as pd
    >>> df = pd.DataFrame(columns=["AREA ", "Inyeccion", "NEUBA II"])
    >>> list(canonizar_columnas(df, ["Area", "Inyección"]).columns)
    ['Area', 'Inyección', 'NEUBA II']
    """
    mapa = {normalizar(c): c for c in canonicas}

    renombres = {
        col: mapa[normalizar(col)]
        for col in df.columns
        if normalizar(col) in mapa and mapa[normalizar(col)] != col
    }

    return df.rename(columns=renombres) if renombres else df.copy()


def cargar_alias(ruta) -> dict[str, str]:
    """
    Lee la tabla de equivalencias de nombres de area.

    La normalizacion resuelve tildes, espacios y mayusculas, pero no resuelve
    que la misma area figure como "Aguada Pichana Este" en una hoja y
    "AP Este" en otra. Para eso va esta tabla: un CSV versionado con dos
    columnas, `alias` y `canonico`, ambas en texto libre (se normalizan aca).

    Si el archivo no existe devuelve un diccionario vacio, asi el pipeline
    corre igual y la tabla se puede ir llenando de a poco.

    Parameters
    ----------
    ruta : str | pathlib.Path

    Returns
    -------
    dict[str, str]
        alias normalizado -> canonico normalizado.
    """
    from pathlib import Path

    ruta = Path(ruta)

    if not ruta.exists():
        return {}

    tabla = pd.read_csv(ruta, comment="#")

    return {
        normalizar(fila.alias): normalizar(fila.canonico)
        for fila in tabla.itertuples()
        if pd.notna(fila.alias) and pd.notna(fila.canonico)
    }


def canonizar_areas(serie: pd.Series, alias: dict[str, str] | None = None) -> pd.Series:
    """
    Normaliza una columna de areas y despues aplica la tabla de alias.

    Este es el unico lugar donde deberia construirse la clave de cruce.

    Parameters
    ----------
    serie : pandas.Series
        Nombres de area como vienen del Excel.
    alias : dict[str, str] | None
        Salida de `cargar_alias`. Si es None no se aplica ninguna equivalencia.

    Returns
    -------
    pandas.Series
    """
    claves = normalizar_serie(serie)

    if alias:
        claves = claves.replace(alias)

    return claves
