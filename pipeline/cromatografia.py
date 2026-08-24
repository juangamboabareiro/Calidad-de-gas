"""
Asignacion de cromatografia a cada fila (Area, Gasoducto).

Por que existe este modulo
--------------------------
Hasta ahora la cromato se pegaba con `merge(premisas_areas, on="Area")` y
despues un `drop_duplicates(['Area','Gasoducto'])`. Eso rompe en dos casos que
hoy conviven en la misma hoja de premisas:

1. **Areas con dos cromatos segun la ruta.** Fortin de Piedra tiene una medicion
   para el gas que pasa por planta (destinos CO Paralelo, NEUII, GPM) y otra
   para el que no (VMS, YPF - RDM, MEGA). En el Excel la clave de busqueda es
   `Area & Sufijo`, donde el sufijo sale del par (Area, Gasoducto). Mergeando
   solo por Area matchean las dos filas y el drop_duplicates se queda con la
   primera, que depende del orden de la hoja. Con los datos de hoy eso le saca
   34,7% del C3+ al pool de MEGA alimentado por areas.

2. **Gasoductos con una fila por destino.** Las premisas de la inyeccion
   secundaria estan repetidas una vez por destino (Pampa SCH x3, YPF - RDM x2,
   etc.). Mergeando por Area sale un producto cartesiano: 3 filas de flujo x 3
   de premisa = 9, que el drop_duplicates vuelve a bajar a 3. Funciona solo
   porque las repeticiones son identicas; el dia que alguien edite una sola, el
   resultado pasa a depender del orden sin avisar.

Como se resuelve
----------------
Dos tablas de premisas y una busqueda en dos etapas:

    por ruta   -> clave (Area, Gasoducto).  Premisas de gasoducto.
    por clave  -> clave Area+Sufijo.        Premisas de area.

Para cada fila se intenta primero la ruta; lo que no matchea cae a la clave.
Sirve igual para yacimientos (nunca matchea por ruta, cae siempre a Area+Sufijo)
y para flujos directos (matchea por ruta). Un solo camino para las dos tablas.

Mientras la columna de destino de las premisas de gasoducto siga vacia, esas
filas caen a la segunda etapa con sufijo vacio, o sea clave = Area: el
comportamiento de hoy, pero con las repeticiones colapsadas explicitamente.

Sobre la normalizacion de las claves de cruce
---------------------------------------------
El nombre de un gasoducto no llega escrito igual desde todos lados: la hoja de
sufijos dice "YPF - RDM", la columna Gasoducto de las tablas dice "YPF RDM", y
algunos destinos ya vienen pasados por `normalizar`. Si `normalizar` no saca los
guiones, "ypf-rdm" y "ypfrdm" son claves distintas y el merge falla en silencio.

Por eso este modulo NO confia en como venga cada tabla: arma sus propias
columnas de cruce con `clave_cruce`, que ademas de `normalizar` saca todo lo que
no sea alfanumerico. Las columnas auxiliares empiezan con "_" y se descartan
antes de devolver, asi que las columnas originales quedan intactas.
"""

from __future__ import annotations

import re

import pandas as pd

from domain.checks import merge_validado
from domain.columnas import COL_AREA, COL_GASODUCTO
from domain.normalizacion import normalizar

COL_SUFIJO = "Sufijo"
COL_CLAVE = "Clave_croma"

_K_AREA = "_k_area"
_K_GAS = "_k_gas"
_K_CLAVE = "_k_clave"

# Sufijos validos hoy. Sirve para detectar si la hoja de sufijos trae fila de
# encabezado (ahi la segunda columna diria "Sufijo" en vez de uno de estos).
SUFIJOS_CONOCIDOS = {"otra", "planta", "tbx"}

# Tolerancia para dar por buena una cromato: 1e-4 es holgado para 14 fracciones
# molares cargadas con 6 decimales.
TOLERANCIA_SUMA_MOLAR = 1e-4

_NO_ALFANUM = re.compile(r"[^0-9a-z]")


def clave_cruce(serie: pd.Series) -> pd.Series:
    """
    Clave de cruce robusta: `normalizar` y despues fuera todo lo no alfanumerico.

    "YPF - RDM", "YPF RDM" y "ypf-rdm" colapsan a "ypfrdm". Se aplica a los dos
    lados de cada merge, siempre desde aca, para que no dependa de por donde
    paso cada tabla.

    Contrapartida: dos nombres distintos podrian colapsar a la misma clave. Si
    aparecen areas o gasoductos nuevos conviene correr
    `domain.checks.detectar_colisiones` con esta funcion.
    """
    return (
        serie.fillna("")
        .astype(str)
        .map(normalizar)
        .str.lower()
        .str.replace(_NO_ALFANUM, "", regex=True)
    )


def cargar_sufijos_planta(path, hoja="Sufijos-Planta"):
    """
    Lee la hoja Sufijos-Planta y parte la clave concatenada en (Area, Gasoducto).

    La hoja viene con la clave del Excel tal cual (`Area & "-" & Gasoducto`) y el
    sufijo al lado:

        Fortin de Piedra-VMS              Otra
        Fortin de Piedra-YPF - RDM        Otra
        Los Toldos II Este-TBX El Porton  TBX

    El separador es el PRIMER guion, porque asi la arma el Excel en
    `Diccionario!Y`. Por eso "Fortin de Piedra-YPF - RDM" parte bien: el " - "
    de adentro de "YPF - RDM" queda del lado del gasoducto.

    Eso se rompe si alguna vez un AREA tiene guion en el nombre (existe
    "El Trapial-Curamched" en premisas, hoy sin sufijo). `validar_sufijos`
    esta para atrapar ese caso.

    Returns
    -------
    pandas.DataFrame
        Columnas Area, Gasoducto, Sufijo. Sin normalizar: la normalizacion de
        las claves de cruce la hace `agregar_cromatografia`.
    """
    crudo = pd.read_excel(path, sheet_name=hoja, header=None, usecols=[0, 1])
    crudo.columns = ["Clave", COL_SUFIJO]

    crudo = crudo.dropna(subset=["Clave", COL_SUFIJO])

    # Si alguien le agrega encabezado a la hoja, la primera fila entraria como
    # dato. Se detecta porque su sufijo no es uno de los conocidos.
    primera = str(crudo.iloc[0][COL_SUFIJO]).strip().lower()
    if primera not in SUFIJOS_CONOCIDOS:
        print(f"[sufijos_planta] se descarta la primera fila (sufijo '{primera}')")
        crudo = crudo.iloc[1:]

    partido = crudo["Clave"].astype(str).str.split("-", n=1, expand=True)

    if partido.shape[1] < 2:
        raise ValueError(
            "[sufijos_planta] hay claves sin guion separador: "
            f"{crudo.loc[partido[0].eq(crudo['Clave']), 'Clave'].tolist()[:5]}"
        )

    sufijos = pd.DataFrame({
        COL_AREA: partido[0].str.strip(),
        COL_GASODUCTO: partido[1].str.strip(),
        COL_SUFIJO: crudo[COL_SUFIJO].astype(str).str.strip().values,
    })

    print(f"[sufijos_planta] {len(sufijos)} pares cargados")

    return sufijos.reset_index(drop=True)


def validar_sufijos(sufijos_planta, premisas_areas, tablas):
    """
    Verifica que el corte de la clave concatenada haya dado nombres reales.

    Si un area tuviera guion en el nombre, el split la partiria al medio y el
    Area resultante no existiria en ningun lado. Este chequeo lo caza antes de
    que el merge simplemente no matchee.

    Parameters
    ----------
    sufijos_planta : pandas.DataFrame
        Salida de `cargar_sufijos_planta`.
    premisas_areas : pandas.DataFrame
        Para sacar el universo de areas conocidas.
    tablas : list[pandas.DataFrame]
        Tablas con columna Gasoducto (yacimientos, flujos directos) para sacar
        el universo de destinos conocidos.

    Raises
    ------
    ValueError
        Si algun Area del corte no existe en premisas.

    Notes
    -----
    La asimetria es a proposito. El riesgo del split esta del lado del Area
    (si tiene guion, queda cortada al medio), y premisas_areas es la lista
    autoritativa: un Area que no figura ahi es un error seguro, asi que
    revienta. Un Gasoducto ausente, en cambio, puede ser legitimo: ese destino
    simplemente no tiene flujo en el periodo modelado. Eso solo se avisa.
    """
    areas = set(clave_cruce(premisas_areas[COL_AREA]))
    destinos = set()
    for tabla in tablas:
        destinos |= set(clave_cruce(tabla[COL_GASODUCTO]))

    fuera_area = sorted(
        set(clave_cruce(sufijos_planta[COL_AREA])) - areas
    )
    fuera_gas = sorted(
        set(clave_cruce(sufijos_planta[COL_GASODUCTO])) - destinos
    )

    if fuera_area:
        raise ValueError(
            f"[validar_sufijos] el corte de la clave dio areas que no existen en "
            f"premisas: {fuera_area}. Probable causa: un area con guion en el nombre."
        )

    if fuera_gas:
        print(
            f"[validar_sufijos] {len(fuera_gas)} destinos del diccionario sin flujo "
            f"en las tablas: {fuera_gas} (puede ser normal si no hay caudal)"
        )

    print(f"[validar_sufijos] ok, {len(sufijos_planta)} pares resuelven a areas reales")


def _colapsar_repetidas(tabla, claves, compuestos, nombre, areas_con_sufijo=None):
    """
    Deja una fila por clave. Distingue dos causas de repeticion.

    **Falta la columna Sufijo.** Si la clave repetida corresponde a un area que
    figura en el diccionario de sufijos, entonces DEBERIA estar desambiguada y
    no lo esta: o la hoja no tiene columna Sufijo o se llama distinto. Eso
    revienta, porque colapsar ahi es exactamente el bug que veniamos a arreglar
    (elegir una de las dos cromatos de Fortin de Piedra segun el orden de la
    hoja).

    **Dato repetido en el origen.** Si la clave repetida NO tiene sufijo posible,
    es una inconsistencia de la hoja de premisas: la misma area cargada dos
    veces con cromatografias distintas (p. ej. Aguada de Castro, C1 = 0,9716 y
    0,8371). No es algo que este modulo pueda resolver. Se avisa y se toma la
    primera, que es lo que hace el VLOOKUP del Excel.

    Las premisas de gasoducto repetidas con valores IDENTICOS se colapsan
    sin drama: es como estan cargadas hoy, una fila por destino.

    Parameters
    ----------
    areas_con_sufijo : set[str] | None
        Claves de cruce de las areas que figuran en `Sufijos-Planta`. Si es
        None no se distingue y todo duplicado con valores distintos avisa.
    """
    repetidas = tabla.duplicated(claves, keep=False)

    if not repetidas.any():
        return tabla

    presentes = [c for c in compuestos if c in tabla.columns]

    distintas = (
        tabla[repetidas].groupby(claves)[presentes].nunique().gt(1).any(axis=1)
    )
    conflictivas = distintas[distintas].index.tolist()

    if conflictivas:
        # groupby con una sola clave devuelve escalares; con varias, tuplas.
        planas = [c if not isinstance(c, tuple) else c[0] for c in conflictivas]

        esperaban_sufijo = sorted(
            c for c in planas if areas_con_sufijo and c in areas_con_sufijo
        )
        inconsistentes = sorted(set(planas) - set(esperaban_sufijo))

        if esperaban_sufijo:
            raise ValueError(
                f"[{nombre}] estas areas tienen dos cromatografias y figuran en "
                f"Sufijos-Planta, pero la hoja de premisas no las desambigua: "
                f"{esperaban_sufijo}. Falta la columna '{COL_SUFIJO}' (en el Excel "
                f"es la columna C de 'Premisas áreas', con valores Planta / Otra / "
                f"TBX). Si en inputs.xlsx se llama distinto, renombrala."
            )

        if inconsistentes:
            print(
                f"[{nombre}] {len(inconsistentes)} areas cargadas dos veces con "
                f"cromatografias DISTINTAS y sin sufijo posible: {inconsistentes}. "
                "Se toma la primera, igual que el VLOOKUP del Excel. Es una "
                "inconsistencia de la hoja de premisas, no de este modulo."
            )
    else:
        print(f"[{nombre}] {int(repetidas.sum())} filas repetidas identicas, se colapsan")

    return tabla.drop_duplicates(claves)


def preparar_premisas(premisas_areas, compuestos, sufijos_planta=None):
    """
    Parte la hoja de premisas en las dos tablas de busqueda.

    Parameters
    ----------
    premisas_areas : pandas.DataFrame
        Hoja Premisas-Areas cruda. Se esperan la columna Area y las de
        compuestos; Sufijo y Gasoducto son opcionales (si no estan, se asumen
        vacias y todo cae a la busqueda por clave).
    compuestos : list[str]
    sufijos_planta : pandas.DataFrame | None
        Salida de `cargar_sufijos_planta`. Sirve para distinguir un duplicado
        que deberia estar desambiguado por sufijo (error de configuracion) de
        uno que es una inconsistencia de la hoja. Muy recomendable pasarlo.

    Returns
    -------
    (premisas_por_ruta, premisas_por_clave) : tuple[pandas.DataFrame, ...]
        La primera indexada por (_k_area, _k_gas), la segunda por _k_clave.
        Ambas con una sola fila por clave.
    """
    premisas = premisas_areas.copy()

    areas_con_sufijo = (
        set(clave_cruce(sufijos_planta[COL_AREA]))
        if sufijos_planta is not None else None
    )

    if COL_SUFIJO not in premisas.columns:
        print(f"[preparar_premisas] la hoja no tiene columna '{COL_SUFIJO}', se asume vacia")
        premisas[COL_SUFIJO] = ""
    premisas[COL_SUFIJO] = premisas[COL_SUFIJO].fillna("")

    if COL_GASODUCTO not in premisas.columns:
        premisas[COL_GASODUCTO] = pd.NA

    tiene_ruta = (
        premisas[COL_GASODUCTO].notna()
        & premisas[COL_GASODUCTO].astype(str).str.strip().ne("")
    )

    columnas = [c for c in compuestos if c in premisas.columns]

    if not columnas:
        raise KeyError("[preparar_premisas] la hoja no tiene ninguna columna de compuesto")

    por_ruta = premisas[tiene_ruta].copy()
    por_ruta[_K_AREA] = clave_cruce(por_ruta[COL_AREA])
    por_ruta[_K_GAS] = clave_cruce(por_ruta[COL_GASODUCTO])
    por_ruta = por_ruta[[_K_AREA, _K_GAS] + columnas]
    por_ruta = _colapsar_repetidas(
        por_ruta, [_K_AREA, _K_GAS], compuestos, "premisas_por_ruta",
        areas_con_sufijo=None,   # por ruta ya esta desambiguado por destino
    )

    por_clave = premisas[~tiene_ruta].copy()
    por_clave[_K_CLAVE] = clave_cruce(
        por_clave[COL_AREA].fillna("").astype(str)
        + por_clave[COL_SUFIJO].astype(str)
    )
    por_clave = por_clave[[_K_CLAVE] + columnas]
    por_clave = _colapsar_repetidas(
        por_clave, [_K_CLAVE], compuestos, "premisas_por_clave",
        areas_con_sufijo=areas_con_sufijo,
    )

    print(
        f"[preparar_premisas] {len(por_ruta)} premisas por ruta, "
        f"{len(por_clave)} por clave"
    )

    return por_ruta, por_clave


def agregar_cromatografia(
    df,
    premisas_por_ruta,
    premisas_por_clave,
    sufijos_planta,
    compuestos,
    *,
    nombre="cromatografia",
):
    """
    Pega las fracciones molares a cada fila (Area, Gasoducto).

    Etapa 1: busqueda por ruta (Area, Gasoducto).
    Etapa 2: para lo que quedo sin match, busqueda por Area+Sufijo.

    Parameters
    ----------
    df : pandas.DataFrame
        Tabla con columnas Area y Gasoducto. No hace falta que vengan
        normalizadas: las claves de cruce se arman aca con `clave_cruce`.
    premisas_por_ruta, premisas_por_clave : pandas.DataFrame
        Salida de `preparar_premisas`.
    sufijos_planta : pandas.DataFrame
        Diccionario (Area, Gasoducto) -> Sufijo, salida de
        `cargar_sufijos_planta`. En el Excel es Diccionario!Y:Z, 10 pares: 6 de
        Fortin de Piedra y 4 de las areas que van a TBX El Porton. Las rutas que
        no figuran usan sufijo vacio.
    compuestos : list[str]

    Returns
    -------
    pandas.DataFrame
        `df` con las columnas de compuestos, Sufijo y Clave_croma agregadas.
        Las filas sin cromato quedan en NaN a proposito: rellenarlas con 0 aca
        haria pasar como "gas sin C3" a algo que en realidad es un dato que
        falta. El fillna(0), si se quiere, va despues y a la vista.
    """
    salida = df.copy()

    salida[_K_AREA] = clave_cruce(salida[COL_AREA])
    salida[_K_GAS] = clave_cruce(salida[COL_GASODUCTO])

    # --- sufijo de planta -------------------------------------------------
    sufijos = sufijos_planta.copy()
    sufijos[_K_AREA] = clave_cruce(sufijos[COL_AREA])
    sufijos[_K_GAS] = clave_cruce(sufijos[COL_GASODUCTO])
    sufijos = sufijos[[_K_AREA, _K_GAS, COL_SUFIJO]]

    salida = merge_validado(
        salida,
        sufijos,
        nombre=f"{nombre}:sufijos",
        on=[_K_AREA, _K_GAS],
        how="left",
        validate="m:1",
        col_ejemplo=COL_AREA,
        reportar=False,          # la enorme mayoria de las rutas no tiene sufijo
    )

    salida[COL_SUFIJO] = salida[COL_SUFIJO].fillna("")
    salida[_K_CLAVE] = clave_cruce(
        salida[COL_AREA].fillna("").astype(str) + salida[COL_SUFIJO].astype(str)
    )
    salida[COL_CLAVE] = salida[_K_CLAVE]

    columnas = [
        c for c in compuestos
        if c in premisas_por_ruta.columns or c in premisas_por_clave.columns
    ]

    # --- etapa 1: por ruta ------------------------------------------------
    ruta = merge_validado(
        salida[[_K_AREA, _K_GAS]],
        premisas_por_ruta,
        nombre=f"{nombre}:por_ruta",
        on=[_K_AREA, _K_GAS],
        how="left",
        validate="m:1",
        reportar=False,          # las areas nunca matchean por ruta, es esperable
    ).reindex(columns=columnas)

    # --- etapa 2: por clave -----------------------------------------------
    clave = merge_validado(
        salida[[_K_CLAVE]],
        premisas_por_clave,
        nombre=f"{nombre}:por_clave",
        on=_K_CLAVE,
        how="left",
        validate="m:1",
        reportar=False,          # se reporta abajo, ya combinado
    ).reindex(columns=columnas)

    # Mascara a nivel FILA, no elemento: si una ruta matcheo, se usa entera.
    # Con combine_first, un compuesto que la hoja deja vacio se rellenaria desde
    # la otra premisa y quedaria una cromato mezcla de dos fuentes.
    tiene_ruta = ruta.notna().any(axis=1)

    croma = ruta.where(tiene_ruta, clave)
    croma.index = salida.index

    salida[columnas] = croma

    _reportar(salida, columnas, tiene_ruta, nombre)

    return salida.drop(columns=[_K_AREA, _K_GAS, _K_CLAVE])


def _reportar(salida, columnas, tiene_ruta, nombre):
    """Avisa que filas quedaron sin cromato y cuales no cierran en 1."""

    sin_croma = salida[columnas].isna().all(axis=1)

    if sin_croma.any():
        ejemplos = (
            salida.loc[sin_croma, [COL_AREA, COL_GASODUCTO]]
            .drop_duplicates()
            .head(8)
            .to_dict("records")
        )
        print(f"[{nombre}] {int(sin_croma.sum())} filas SIN cromatografia | ej: {ejemplos}")

    suma = salida[columnas].sum(axis=1)
    mal = (~sin_croma) & (suma - 1).abs().gt(TOLERANCIA_SUMA_MOLAR)

    if mal.any():
        peor = salida.loc[mal].assign(_suma=suma[mal]).nlargest(3, "_suma")
        ejemplos = peor[[COL_AREA, COL_GASODUCTO, "_suma"]].to_dict("records")
        print(f"[{nombre}] {int(mal.sum())} filas con suma molar != 1 | ej: {ejemplos}")

    print(
        f"[{nombre}] {int(tiene_ruta.sum())} por ruta, "
        f"{int((~tiene_ruta & ~sin_croma).sum())} por clave, "
        f"{int(sin_croma.sum())} sin resolver"
    )
