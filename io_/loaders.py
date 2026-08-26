"""
Lectura de las hojas de inputs.

Ver INPUTS en Data_Dictionary.md

Responsabilidad de este modulo
------------------------------
Devolver tablas cuya CLAVE ya es confiable. Concretamente, al leer se hacen
dos cosas que antes estaban desparramadas por el pipeline:

1. Se canonizan los encabezados: si alguien escribe "Inyeccion" sin tilde o
   "AREA " con espacio, igual llegan como la constante que espera el codigo.
2. Se canoniza la columna Area: normalizacion + tabla de alias.

Con eso, ninguna funcion aguas abajo tiene que acordarse de normalizar, y
un merge que falla ya no puede deberse a como tipeo el nombre una persona.

Lo que este modulo NO hace: rellenar nulos, filtrar filas ni calcular nada.
Eso sigue siendo de `preprocesamiento`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from domain.columnas import (
    COL_AREA,
    COL_COEF_INYECCION,
    COL_CUENCA,
    COL_GASODUCTO,
    COL_HUB,
    COL_INYECCION,
    COL_VOLUMEN,
)
from domain.normalizacion import canonizar_areas, canonizar_columnas, cargar_alias

# Tabla de equivalencias de nombres de area. Se carga una sola vez.
RUTA_ALIAS = Path(__file__).resolve().parent.parent / "datos" / "alias_areas.csv"
ALIAS_AREAS = cargar_alias(RUTA_ALIAS)

# Encabezados que el codigo espera encontrar. Cualquier variante ortografica
# de estos se corrige al leer; el resto de las columnas queda intacto.
CANONICAS = [
    COL_AREA,
    COL_CUENCA,
    COL_GASODUCTO,
    COL_HUB,
    COL_INYECCION,
    COL_VOLUMEN,
    COL_COEF_INYECCION,
    "Compuesto",
    "Planta",
]


def _leer(
    path,
    hoja: str,
    *,
    canonizar_area: bool = True,
    **kwargs,
) -> pd.DataFrame:
    """
    Lector base: lee la hoja, corrige encabezados y canoniza Area.

    Parameters
    ----------
    path : str | pathlib.Path
        Ruta del Excel de inputs.
    hoja : str
        Nombre de la hoja.
    canonizar_area : bool
        Poner en False para obtener los nombres ORIGINALES, por ejemplo para
        correr `checks.detectar_colisiones` o para armar la tabla de alias.
    **kwargs
        Se pasan a `pandas.read_excel` (index_col, etc.).

    Returns
    -------
    pandas.DataFrame
    """
    df = pd.read_excel(path, sheet_name=hoja, **kwargs)

    df = canonizar_columnas(df, CANONICAS)

    if canonizar_area and COL_AREA in df.columns:
        df[COL_AREA] = canonizar_areas(df[COL_AREA], ALIAS_AREAS)

    return df


# --------------------------------------------------------------------------
# Hojas con columna Area (se canoniza sola)
# --------------------------------------------------------------------------

def load_mapa(path, **kw):
    return _leer(path, "Mapa", index_col="Num", **kw)


def load_inyeccion_9300(path, **kw):
    return _leer(path, "Inyeccion-9300", **kw)


def load_coeficientes(path, **kw):
    return _leer(path, "Coeficientes", **kw)


def load_premisas_areas(path, **kw):
    return _leer(path, "Premisas-Areas", **kw)


def load_flujos_directos(path, **kw):
    return _leer(path, "Flujos-Directos", **kw)


def load_yacimientos(path, **kw):
    return _leer(path, "Yacimientos", **kw)


def load_detalles_hubs(path, **kw):
    return _leer(path, "Detalles-HUBs", **kw)


def load_coefs_inyeccion_area(path, **kw):
    return _leer(path, "Coefs-Iny-Areas", **kw)


def load_plantas_yacimientos(path, **kw):
    return _leer(path, "Plantas-Yacimientos", **kw)



def load_cromas_hubs(path, **kw):
    """
    Hoja Cromas-HUBs: una fila por hub con la croma del gas que SALE del hub.

    Es OPCIONAL: si la hoja no existe se devuelve None y el ruteo por hubs
    cae a la mezcla volumetrica de las areas de cada hub (con aviso). Asi el
    pipeline corre igual mientras la hoja se va cargando.
    """
    try:
        return _leer(path, "Cromas-HUBs", **kw)
    except ValueError:
        print("[loaders] inputs sin hoja Cromas-HUBs: las cromas de hub "
              "se calculan como mezcla volumetrica")
        return None


# --------------------------------------------------------------------------
# Hojas sin columna Area
# --------------------------------------------------------------------------

def load_propiedades(path, **kw):
    return _leer(path, "Propiedades", **kw)


def load_constantes_gas(path, **kw):
    return _leer(path, "Constantes-GAS", **kw)


def load_retenidos_rtp(path, **kw):
    return _leer(path, "Retenidos-RTP", **kw)


def load_matriz_inyecciones(path, **kw):
    """
    Matriz origen-destino, en formato ancho.

    Ojo: aca las areas NO estan en una columna Area sino repartidas como
    VALORES a lo ancho (una columna por gasoducto). Por eso `_leer` no puede
    canonizarlas y el trabajo queda en `preprocesamiento`, despues del melt.
    """
    return _leer(path, "Matriz-Inyecciones", index_col="Num", **kw)
