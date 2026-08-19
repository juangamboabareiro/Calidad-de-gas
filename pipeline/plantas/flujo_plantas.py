import pandas as pd


def calcular_lgn_unitario(vol_referencia, retenidos_vol):
    """LGN (tn/d) por unidad de Volumen_inyectado.

    retenidos_vol es lineal en el volumen tratado (a composicion y coeficientes
    de retencion fijos), asi que este ratio permite pasar de gas a liquido y al
    reves sin re-modelar la planta.

    vol_referencia : float
        Volumen con el que se calculo retenidos_vol (todo el pool disponible).
    """

    vol_referencia = float(vol_referencia)

    if vol_referencia <= 0:
        return 0.0

    return float(retenidos_vol.values.sum()) / vol_referencia


def calcular_volumen_maximo(lgn_unitario, CAPACIDAD_EVACUACION_PLANTA, CAPACIDAD_INGRESO_PLANTA=None):
    """Cuanto gas puede tomar la planta antes de "llenarse".

    LLENARSE = agotar la capacidad de EVACUACION DE LGN. Es la restriccion
    activa; el ingreso de gas rara vez limita, pero si se pasa
    CAPACIDAD_INGRESO_PLANTA se toma el menor de los dos.

        vol_max = CAPACIDAD_EVACUACION_PLANTA / lgn_unitario

    Si lgn_unitario <= 0 (planta sin retencion, o retenidos negativos por
    coeficientes corregidos) no hay restriccion de liquido: devuelve inf, o la
    capacidad de ingreso si esta definida.
    """

    if lgn_unitario <= 0:
        vol_max = float('inf')
    else:
        vol_max = float(CAPACIDAD_EVACUACION_PLANTA) / float(lgn_unitario)

    if CAPACIDAD_INGRESO_PLANTA is not None:
        vol_max = min(vol_max, float(CAPACIDAD_INGRESO_PLANTA))

    return vol_max


def repartir_flujo_planta(vol_disponible, vol_maximo, MAX_DERIVACION_PLANTA_A_PLANTA=0.0):
    """Reparte el gas que llega a un eslabon de la cascada.

    ORDEN (definido 19/8): la planta se LLENA hasta su capacidad de evacuacion,
    lo que sobra se DERIVA a la planta siguiente para que igual se trate, y lo
    que ni la derivacion se lleva es BYPASS.

        vol_asignado = min(vol_disponible, vol_maximo)
        sobrante     = vol_disponible - vol_asignado
        vol_derivado = min(sobrante, MAX_DERIVACION_PLANTA_A_PLANTA)
        bypass       = sobrante - vol_derivado

    Vale la identidad, que es la que cierra el balance del eslabon:

        vol_disponible == vol_asignado + vol_derivado + bypass

    MAX_DERIVACION_PLANTA_A_PLANTA : float
        Tope del traspaso hacia la planta siguiente, en unidades de
        Volumen_inyectado. 0.0 para el ultimo eslabon (MEGA): ahi todo el
        sobrante es bypass.
    """

    vol_disponible = max(float(vol_disponible), 0.0)

    vol_asignado = min(vol_disponible, float(vol_maximo))

    sobrante = vol_disponible - vol_asignado

    vol_derivado = min(sobrante, float(MAX_DERIVACION_PLANTA_A_PLANTA))

    bypass = sobrante - vol_derivado

    return {
        'vol_disponible': vol_disponible,
        'vol_maximo': float(vol_maximo),
        'vol_asignado': vol_asignado,
        'sobrante': sobrante,
        'vol_derivado': vol_derivado,
        'bypass': bypass,
        'ocupacion': (vol_asignado / vol_maximo) if vol_maximo not in (0, float('inf')) else None,
    }


def calcular_DERIVACION(flujos_origen, gas_rico_IN_origen, nombre_origen='derivacion'):
    """Empaqueta el gas que una planta le pasa a la SIGUIENTE en la cascada.

    Solo hace falta cuando el destino tiene un pool propio de otra composicion
    (el caso TTY-DP -> MEGA): ahi el gas derivado se inyecta como una fila mas
    de input dentro de io_plantas, antes de calcular Volumen_relativo, para que
    entre en la mezcla que forma gas_rico_IN.

    Cuando origen y destino comparten el mismo pool (TTY-TBX -> TTY-DP, que son
    dos trenes sobre el mismo gas) NO hace falta: la cromato es identica y basta
    con pasarle a la planta destino el volumen via vol_disponible.

    El gas derivado sale SIN TRATAR, entonces su cromato es la del gas rico de
    ENTRADA de la planta origen. gas_rico_IN_origen es una Series.
    """

    return {
        'vol_derivacion': flujos_origen['vol_derivado'],
        'cromato_derivacion': gas_rico_IN_origen,
        'origen': nombre_origen,
    }
