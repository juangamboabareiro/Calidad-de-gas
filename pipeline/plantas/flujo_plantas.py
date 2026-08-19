import pandas as pd


def calcular_flujos_planta(tabla_planta, retenidos_vol, CAPACIDAD_EVACUACION_PLANTA, MAX_DERIVACION_PLANTA_A_PLANTA=0.0, factor_retenidos=1.0, capacidad_libre_destino=None):
    """Reparte el gas que llega a una planta entre procesado / derivado / bypass.

    QUE LIMITA: la capacidad de EVACUACION DE LGN, no la de ingreso de gas. La
    capacidad de ingreso es holgada, entonces no aparece en este calculo (queda
    solo para el KPI de ocupacion). Como el gas hay que tratarlo lo mas posible
    para poder comercializarlo, el orden de prioridades es:

        1. tratar todo lo que la evacuacion de LGN permita
        2. DERIVAR el excedente a otra planta, para que igual se trate
        3. BYPASEAR solo lo que ni derivando entra

    COMO SE PASA DE LIQUIDO A GAS: retenidos_vol es lineal en Volumen_inyectado
    (a composicion y coeficientes de retencion fijos), asi que el excedente de
    LGN se traduce a volumen de gas con una regla de tres:

        lgn_potencial     = retenidos_vol.sum() * factor_retenidos
        fraccion_tratable = CAPACIDAD_EVACUACION_PLANTA / lgn_potencial
        vol_procesado     = vol_entrante * fraccion_tratable
        excedente         = vol_entrante - vol_procesado
        vol_derivado      = min(excedente, MAX_DERIVACION[, capacidad_libre_destino])
        bypass            = excedente - vol_derivado

    Vale la identidad, que es la que cierra el balance:

        vol_entrante == vol_procesado + vol_derivado + bypass

    Se asume que el gas que se deriva y el que bypasea tienen la misma cromato
    que el promedio de entrada (gas_rico_IN): se separa un caudal, no un corte.

    tabla_planta : DataFrame
        Tabla de input de la planta YA con las derivaciones entrantes sumadas
        (o sea, la que devuelve io_plantas): propio + derivado de la anterior.

    retenidos_vol : DataFrame
        LGN que produciria la planta si tratara TODO el volumen entrante, con
        los coeficientes de retencion YA corregidos si hubo correccion. El orden
        importa: primero se corrige la recuperacion (tratar lo mas posible) y
        recien despues se deriva lo que sigue sin entrar.

    CAPACIDAD_EVACUACION_PLANTA : float
        Limite de evacuacion de LGN, en la unidad en que quede expresado
        retenidos_vol.sum() * factor_retenidos.

    factor_retenidos : float
        Conversion de retenidos_vol a la unidad de CAPACIDAD_EVACUACION_PLANTA.
        Hoy cada planta usa una unidad distinta (TTY-DP compara el crudo contra
        200, MEGA divide por 1000 contra 5600, TBX venia en barriles contra
        7700) -> este factor lo hace explicito en vez de esconderlo en el if.
        El cociente es adimensional, pero SOLO si los dos lados usan la misma
        unidad: si el factor esta mal, fraccion_tratable sale mal.

    MAX_DERIVACION_PLANTA_A_PLANTA : float
        Tope de la derivacion hacia la planta siguiente, en la MISMA unidad que
        tabla_planta['Volumen_inyectado']. 0.0 para MEGA, que no deriva.

    capacidad_libre_destino : float | None
        Tope opcional adicional: cuanto gas mas puede absorber el destino. Si es
        None no se topea, y se puede derivar gas que el destino tampoco trata
        (le va a aparecer como bypass alla en vez de aca).
    """

    vol_entrante = float(tabla_planta['Volumen_inyectado'].values.sum())

    lgn_potencial = float(retenidos_vol.values.sum()) * float(factor_retenidos)

    sin_excedente = {
        'vol_entrante': vol_entrante,
        'lgn_potencial': lgn_potencial,
        'fraccion_tratable': 1.0,
        'vol_procesado': vol_entrante,
        'excedente': 0.0,
        'vol_derivado': 0.0,
        'bypass': 0.0,
    }

    if vol_entrante <= 0 or lgn_potencial <= 0:
        return sin_excedente

    if lgn_potencial <= CAPACIDAD_EVACUACION_PLANTA:
        return sin_excedente

    fraccion_tratable = float(CAPACIDAD_EVACUACION_PLANTA) / lgn_potencial

    vol_procesado = vol_entrante * fraccion_tratable

    excedente = vol_entrante - vol_procesado

    tope_derivacion = float(MAX_DERIVACION_PLANTA_A_PLANTA)

    if capacidad_libre_destino is not None:
        tope_derivacion = min(tope_derivacion, max(float(capacidad_libre_destino), 0.0))

    vol_derivado = min(excedente, tope_derivacion)

    bypass = excedente - vol_derivado

    return {
        'vol_entrante': vol_entrante,
        'lgn_potencial': lgn_potencial,
        'fraccion_tratable': fraccion_tratable,
        'vol_procesado': vol_procesado,
        'excedente': excedente,
        'vol_derivado': vol_derivado,
        'bypass': bypass,
    }


def calcular_DERIVACION(flujos_origen, gas_rico_IN_origen, nombre_origen='derivacion'):
    """Volumen y cromatografia del gas que una planta deriva hacia la siguiente.

    El volumen ya viene resuelto por calcular_flujos_planta (excedente de LGN
    traducido a gas, topeado por MAX_DERIVACION), aca solo se lo empaqueta con
    la cromato.

    El resultado se le pasa a modelar_TTY / modelar_MEGA de la planta DESTINO
    via derivaciones=[...], que lo inyecta como una fila mas de input dentro de
    io_plantas. Agregarlo a mano a la tabla ya devuelta no sirve: io_plantas
    reconstruye la tabla desde tabla_total_flujos_directos y la fila se pierde.

    nombre_origen : str
        Va a la columna 'Area' de la fila generada, para poder identificarla
        en la tabla de la planta destino.
    """

    # El gas derivado sale SIN TRATAR, entonces su cromato es la del gas rico de
    # entrada de la planta origen. gas_rico_IN_origen es una Series.
    return {
        'vol_derivacion': flujos_origen['vol_derivado'],
        'cromato_derivacion': gas_rico_IN_origen,
        'origen': nombre_origen,
    }
