import pandas as pd


def calcular_BYPASS(tabla_planta, CAPACIDAD_EVACUACION_PLANTA, CAPACIDAD_PLANTA):

    BYPASS = tabla_planta['Volumen_inyectado'].values.sum() - CAPACIDAD_EVACUACION_PLANTA + max(tabla_planta['Volumen_inyectado'].values.sum() - CAPACIDAD_PLANTA, 0)

    return BYPASS



def calcular_DERIVACION(tabla_planta_origen, gas_rico_IN_origen, CAPACIDAD_EVACUACION_PLANTA, MAX_DERIVACION_PLANTA_A_PLANTA, nombre_origen='derivacion'):
    """Volumen y cromatografia del gas que una planta deriva hacia otra.

    El resultado se le pasa a modelar_TTY / modelar_MEGA de la planta DESTINO
    via derivaciones=[...], que lo inyecta como una fila mas de input dentro de
    io_plantas. Agregarlo a mano a la tabla ya devuelta no sirve: io_plantas
    reconstruye la tabla desde tabla_total_flujos_directos y la fila se pierde.

    nombre_origen : str
        Va a la columna 'Area' de la fila generada, para poder identificarla
        en la tabla de la planta destino.
    """

    VOL_DERIVACION = min(max(tabla_planta_origen['Volumen_inyectado'].values.sum() - CAPACIDAD_EVACUACION_PLANTA, 0), MAX_DERIVACION_PLANTA_A_PLANTA)

    # gas_rico_IN_origen es una Series -> el .T anterior era un no-op.
    # La cromato del gas derivado es la del gas rico de ENTRADA de la planta
    # origen (se deriva antes de tratar).
    CROMATO_DERIVACION = gas_rico_IN_origen

    return {'vol_derivacion' : VOL_DERIVACION, 'cromato_derivacion' : CROMATO_DERIVACION, 'origen' : nombre_origen}
