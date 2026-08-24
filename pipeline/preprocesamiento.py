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
from pipeline.cromatografia import clave_cruce


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


def validar_destinos_matriz(
    matriz_inyecciones: pd.DataFrame,
    destinos_validos,
    nombre: str = "matriz_inyecciones",
) -> pd.DataFrame:
    """
    Deja en la matriz ancha solo las columnas que son destinos reales.

    El filtro es lo barato. Lo que importa es el reporte de FALTANTES: un
    destino declarado que no esta como columna significa que todas las areas
    que inyectan ahi pierden ese flujo en silencio, porque el par
    (Area, Gasoducto) nunca se genera en el melt de
    `preparar_matriz_inyecciones`.

    Eso fue exactamente lo que paso con BdP, TBX El Porton y VM LIQ: 9 areas
    (Agua del Cajon, Sierra Chata, Bajo del Toro, El Porton, Narambuena,
    Los Toldos II Este, LNG 1/2/3) quedaron sin destino, y `inyeccion_area` las
    reportaba como "areas sin destino en la matriz" sin poder decir por que.

    Un blacklist de columnas basura ("Unnamed: 19", "Observaciones") no lo
    habria detectado: el problema no era lo que sobraba sino lo que faltaba.
    Solo aparece comparando contra una lista independiente.

    El tercer caso que reporta, `solo_formato`, importa porque
    `query_coef_inyeccion_tabla_total` mergea por [Area, Gasoducto] con string
    exacto: si el nombre de una columna de la matriz y el Gasoducto de los
    coefs difieren en un espacio, ese flujo ya se pierde hoy sin avisar.

    Parameters
    ----------
    matriz_inyecciones : pandas.DataFrame
        La matriz cruda, ancha: una columna por destino, las areas de origen
        como valores.
    destinos_validos : iterable[str]
        Lista de referencia. Hoy sale de `coefs_inyeccion_area[Gasoducto]`.
        Ver las notas para las alternativas.
    nombre : str
        Etiqueta para los mensajes.

    Returns
    -------
    pandas.DataFrame
        La matriz con las columnas de destino unicamente.

    Notes
    -----
    Usar los coefs como fuente tiene una asimetria que conviene tener presente:
    hace que la ESTRUCTURA dependa de los VALORES. Si alguien agrega un destino
    a la matriz sin cargarle coeficientes, este chequeo lo descarta como basura
    en vez de avisar que faltan los coefs, que es al reves de lo deseable.

    Alternativas para cuando se discuta normalizacion de datos con el equipo:
    una hoja `Destinos` en inputs.xlsx (fuente unica declarada, revisable, dato
    y no codigo), o una lista en config.py. Y si algun dia la matriz es la
    fuente confiable, el chequeo se invierte: la matriz declara los destinos y
    lo que se valida es que los coefs los cubran a todos.

    Emparenta con `comunes.columnas_gasoductos`, que resuelve el mismo problema
    para el melt de yacimientos y flujos directos. La diferencia es que aquella
    infiere desde el propio DataFrame (ve lo que sobra, no lo que falta) y esta
    compara contra una lista externa. Son dos niveles del mismo chequeo.
    """
    columnas = list(matriz_inyecciones.columns)
    validos = list(dict.fromkeys(destinos_validos))

    k_col = {c: k for c, k in zip(columnas, clave_cruce(pd.Series(columnas)))}
    k_val = {v: k for v, k in zip(validos, clave_cruce(pd.Series(validos)))}

    claves_validas = set(k_val.values())
    claves_columnas = set(k_col.values())

    # Match exacto de string: es lo que necesita el merge de coefs mas adelante.
    exactos = [c for c in columnas if c in validos]

    solo_formato = [
        (c, v)
        for c in columnas
        if c not in validos
        for v in validos
        if k_col[c] == k_val[v]
    ]

    basura = [c for c in columnas if k_col[c] not in claves_validas]
    faltantes = [v for v in validos if k_val[v] not in claves_columnas]

    if faltantes:
        print(
            f"[{nombre}] FALTAN {len(faltantes)} destinos como columna: {faltantes}. "
            "Las areas que inyectan ahi pierden ese flujo entero."
        )

    if solo_formato:
        print(
            f"[{nombre}] {len(solo_formato)} columnas coinciden solo por formato "
            f"(el merge de coefs las va a perder): {solo_formato}"
        )

    if basura:
        print(
            f"[{nombre}] se descartan {len(basura)} columnas que no son destino: {basura}"
        )

    return matriz_inyecciones[exactos]


def preparar_matriz_inyecciones(
    matriz_inyecciones: pd.DataFrame,
    destinos_validos=None,
) -> pd.DataFrame:
    """
    Pasa la matriz origen-destino a formato largo.

    En la hoja original cada columna es un gasoducto y sus celdas son las
    areas que inyectan ahi. Como las columnas tienen distinto largo, sobran
    celdas vacias al pie: esas filas se descartan (antes habia un
    `matriz_inyecciones.fillna('error')` que no hacia nada, porque no se
    asignaba el resultado).

    Parameters
    ----------
    matriz_inyecciones : pandas.DataFrame
        Hoja Matriz-Inyecciones, ancha.
    destinos_validos : iterable[str] | None
        Si se pasa, se valida contra esa lista antes del melt y se descartan
        las columnas que no son destino. Si es None se meltea todo, que es el
        comportamiento viejo: las columnas descriptivas (Area, Inyección,
        Observaciones, Unnamed: N) terminan convertidas en gasoductos.

    Returns
    -------
    pandas.DataFrame
        Columnas: Gasoducto, Area.
    """
    if destinos_validos is not None:
        matriz_inyecciones = validar_destinos_matriz(
            matriz_inyecciones, destinos_validos
        )

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

    # Los destinos se sacan de los coefs mientras Gasoducto todavia es una
    # columna, o sea ANTES de `preparar_coefs_inyeccion_area`.
    destinos = coefs_inyeccion_area[COL_GASODUCTO].dropna().unique()

    return {
        "flujos_directos": rellenar_numericos(flujos_directos),
        "yacimientos": rellenar_numericos(yacimientos),
        "detalles_hubs": rellenar_numericos(detalles_hubs),
        "plantas_yacimientos": plantas_yacimientos.copy(),
        "premisas_areas": premisas_areas.copy(),
        "propiedades": preparar_propiedades(propiedades),
        "matriz_inyecciones": preparar_matriz_inyecciones(
            matriz_inyecciones, destinos_validos=destinos
        ),
        "coefs_inyeccion_area": preparar_coefs_inyeccion_area(coefs_inyeccion_area),
    }
