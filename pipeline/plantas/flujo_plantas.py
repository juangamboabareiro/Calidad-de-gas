import pandas as pd
#AGREGAR LOS VOLUMENES NUEVOS SEGUN DERIVACION Y SERIA TO DO POR AHORA HABRIA QUE VER TEMA SIGNOS Y ESAS PORQUERIAS

def calcular_BYPASS(tabla_planta, CAPACIDAD_EVACUACION_PLANTA, CAPACIDAD_PLANTA):

    BYPASS = tabla_planta['Volumen_inyectado'].values.sum() - CAPACIDAD_EVACUACION_PLANTA + max(tabla_planta['Volumen_inyectado'].values.sum() - CAPACIDAD_PLANTA, 0)

    return BYPASS



def calcular_DERIVACION(tabla_planta_origen, gas_rico_IN_origen, CAPACIDAD_EVACUACION_PLANTA, MAX_DERIVACION_PLANTA_A_PLANTA):

    VOL_DERIVACION = min(max(tabla_planta_origen['Volumen_inyectado'].values.sum() - CAPACIDAD_EVACUACION_PLANTA, 0), MAX_DERIVACION_PLANTA_A_PLANTA)

    CROMATO_DERIVACION = gas_rico_IN_origen.T 

    return {'vol_derivacion' : VOL_DERIVACION, 'cromato_derivacion' : CROMATO_DERIVACION}




# TTY_DP --> DERIVACION_TTY --> IF(gas_rico_IN_TTY_DP = 0, gas_rico_IN_TTY_TBX, gas_rico_IN_TTY_DP)

# MEGA --> IF(derivacion_TTY_DP_CROMA = 0, gas_rico_IN_TTY_TBX,derivacion_TTY_DP_CROMA)

# Para las cromas de los bypass seria la misma que la de gas_rico_IN









