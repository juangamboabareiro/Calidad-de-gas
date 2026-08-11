import unicodedata
import pandas as pd
import numpy as np


def normalizar(texto):
    """
    Normaliza una cadena de texto para facilitar comparaciones.

    La función:
    - Convierte el texto a minúsculas.
    - Elimina espacios al inicio y al final.
    - Elimina tildes y otros signos diacríticos.
    - Conserva únicamente caracteres alfanuméricos.

    Parameters
    ----------
    texto : str | any
        Valor a normalizar. Si es nulo (NaN), se devuelve sin modificar.

    Returns
    -------
    str | any
        Texto normalizado o el valor original si es NaN.

    Examples
    --------
    normalizar("Gas Rico")
    'gasrico'

    normalizar("Área_1")
    'area1'

    normalizar("  Cañadón Seco  ")
    'canadonseco'
    """

    if pd.isna(texto):
        return texto

    texto = str(texto).strip().lower()

    # sacar tildes
    texto = ''.join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )

    # dejar solo letras y números
    texto = ''.join(c for c in texto if c.isalnum())

    return texto


def asignar_estacion(mes):
    """
    Clasifica un mes como verano o invierno según el criterio operativo.

    Parameters
    ----------
    mes : int
        Número de mes (1-12).

    Returns
    -------
    str
        'verano' para los meses 1, 2, 3, 4, 11 y 12.
        'invierno' para los meses 5, 6, 7, 8 y 9.
        'error' si el valor no corresponde a un mes válido.
    """

    if mes in [1, 2, 3, 4, 11, 12]:
        return "verano"

    if mes in [5, 6, 7, 8, 9]:
        return "invierno"

    return "error"

