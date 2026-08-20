"""
Chequeos y merges instrumentados.

El problema que resuelve este modulo: en pandas, un merge que multiplica filas
(porque la tabla derecha tiene la clave repetida) o que no matchea nada
(porque la clave esta mal normalizada) NO tira error. Te devuelve un DataFrame
perfectamente valido con numeros mal. Todo el historial de "no me matchean los
resultados" del changelog es alguna variante de esto.

`merge_validado` envuelve el merge y:
  - te avisa si la cantidad de filas cambio,
  - te avisa cuantas filas de la izquierda no encontraron pareja (y ejemplos),
  - opcionalmente exige una cardinalidad con `validate=` (levanta excepcion).
"""

from __future__ import annotations

import pandas as pd


# Si lo pones en False, merge_validado no imprime nada (util en produccion
# o cuando corres el pipeline dentro de Streamlit).
VERBOSE = True


def merge_validado(
    izquierda: pd.DataFrame,
    derecha: pd.DataFrame,
    *,
    nombre: str,
    how: str = "left",
    validate: str | None = None,
    mostrar_ejemplos: int = 5,
    col_ejemplo: str | None = None,
    reportar: bool | None = None,
    **kwargs,
) -> pd.DataFrame:
    """
    Merge con reporte de filas sin match y verificacion de cardinalidad.

    Parameters
    ----------
    izquierda, derecha : pandas.DataFrame
        Tablas a unir.
    nombre : str
        Etiqueta para identificar este merge en los mensajes.
    how : str
        Igual que en pandas.merge.
    validate : str | None
        Cardinalidad esperada: "1:1", "1:m", "m:1" o "m:m".
        Si la realidad no la cumple, pandas levanta MergeError.
        Usar "m:1" cuando la tabla derecha es un diccionario/lookup
        (una fila por clave) es lo mas comun aca.
    mostrar_ejemplos : int
        Cuantas claves sin match imprimir.
    col_ejemplo : str | None
        De que columna sacar los ejemplos. Por defecto usa la primera
        clave de cruce.
    reportar : bool | None
        Silencia o fuerza el reporte solo para esta llamada. Sirve para los
        merges donde las filas sin match son esperables y su ruido tapa a los
        merges que si importan. None = usar el VERBOSE del modulo.
    **kwargs
        `on`, `left_on`, `right_on`, `suffixes`, etc.

    Returns
    -------
    pandas.DataFrame
        El resultado del merge, sin columnas auxiliares.
    """
    filas_antes = len(izquierda)

    resultado = izquierda.merge(
        derecha,
        how=how,
        validate=validate,
        indicator="_origen",
        **kwargs,
    )

    filas_despues = len(resultado)

    # Un cambio en la cantidad de filas NUNCA es esperable en estos merges:
    # significa duplicacion. Se avisa siempre, aunque el llamador silencie
    # el resto.
    if VERBOSE and filas_despues != filas_antes:
        print(
            f"[{nombre}] OJO filas {filas_antes} -> {filas_despues} "
            f"(diferencia: {filas_despues - filas_antes:+d})"
        )

    if VERBOSE if reportar is None else reportar:
        sin_match = resultado["_origen"] == "left_only"

        if sin_match.any():
            if col_ejemplo is None:
                claves = kwargs.get("on") or kwargs.get("left_on")
                col_ejemplo = claves[0] if isinstance(claves, list) else claves

            ejemplos = ""
            if col_ejemplo in resultado.columns:
                valores = resultado.loc[sin_match, col_ejemplo].unique()
                ejemplos = f" | ej: {list(valores[:mostrar_ejemplos])}"

            print(f"[{nombre}] {sin_match.sum()} filas sin match{ejemplos}")

    return resultado.drop(columns="_origen")


def avisar_duplicados(df: pd.DataFrame, claves: list[str], nombre: str) -> pd.DataFrame:
    """
    Reporta filas con la combinacion de `claves` repetida.

    Correr esto sobre una tabla ANTES de usarla como lado derecho de un merge
    te dice de antemano si el merge va a multiplicar filas.

    Returns
    -------
    pandas.DataFrame
        Las filas duplicadas (vacio si esta todo bien).
    """
    duplicados = df[df.duplicated(subset=claves, keep=False)].sort_values(claves)

    if VERBOSE and len(duplicados):
        print(f"[{nombre}] {len(duplicados)} filas con {claves} repetido")

    return duplicados


def detectar_colisiones(serie: pd.Series, normalizador) -> pd.DataFrame:
    """
    Encuentra nombres distintos que colapsan a la misma clave normalizada.

    Ejemplo del riesgo: si existieran las areas "El Mangrullo" y "El Mangrulló",
    ambas normalizan a "elmangrullo" y el merge las trata como la misma,
    sumando volumenes que no van juntos.

    Parameters
    ----------
    serie : pandas.Series
        Columna con los nombres ORIGINALES (sin normalizar).
    normalizador : callable
        Tipicamente `domain.normalizacion.normalizar`.

    Returns
    -------
    pandas.DataFrame
        Columnas 'original' y 'clave', solo para las claves conflictivas.
    """
    df = pd.DataFrame({"original": pd.Series(serie.dropna().unique())})
    df["clave"] = df["original"].map(normalizador)

    return df[df.duplicated("clave", keep=False)].sort_values("clave")
