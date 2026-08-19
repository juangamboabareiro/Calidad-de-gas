# region MODULOS

import pandas as pd
import numpy as np
from domain.ctes_gas import *
from pipeline.preprocesamiento import preprocesar_inputs
import config
from pipeline.inyeccion_std import calcular_inyeccion_std
from io_.loaders import load_inyeccion_9300, load_coeficientes, load_retenidos_rtp
from pipeline.inyeccion_area import calcular_inyeccion_area, calcular_inyeccion
from pipeline.yacimientos import calcular_inyeccion_yacimientos_areas
from pipeline.detalles_hubs import calcular_inyeccion_detalles_hubs
from pipeline.flujos_directos import calcular_inyeccion_flujos_directos
from pipeline.tabla_total import calcular_tabla_total_yacimientos, calcular_tabla_total_flujos_directos, calcular_tabla_total_detalles_hubs
from domain.propiedades_gas import calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.MEGA import modelar_MEGA
from pipeline.plantas.TTY import modelar_TTY
from outputs.writers import guardar
from io_.loaders import load_flujos_directos, load_yacimientos, load_detalles_hubs, load_propiedades, load_plantas_yacimientos, load_matriz_inyecciones, load_premisas_areas, load_coefs_inyeccion_area, load_retenidos_rtp
from pipeline.plantas.flujo_plantas import calcular_BYPASS, calcular_DERIVACION




# endregion



# region inputs

inyeccion_9300 = load_inyeccion_9300(config.PATH_INPUTS)
coeficientes = load_coeficientes(config.PATH_INPUTS)
retenidos_RTP = load_retenidos_rtp(config.PATH_INPUTS)

flujos_directos = load_flujos_directos(config.PATH_INPUTS)
yacimientos = load_yacimientos(config.PATH_INPUTS)
detalles_hubs = load_detalles_hubs(config.PATH_INPUTS)
propiedades = load_propiedades(config.PATH_INPUTS)
plantas_yacimientos = load_plantas_yacimientos(config.PATH_INPUTS)

#endregion



# region preprocesamiento de datos

flujos_directos, yacimientos, detalles_hubs, propiedades, plantas_yacimientos, matriz_inyecciones, coefs_inyeccion_area, premisas_areas = preprocesar_inputs(flujos_directos=flujos_directos, yacimientos=yacimientos, detalles_hubs=detalles_hubs, propiedades=propiedades, plantas_yacimientos=plantas_yacimientos)

# endregion


guardar(flujos_directos, 'pre_flujos_directos.csv')

inyeccion_std = calcular_inyeccion_std(inyeccion_9300, coeficientes)
inyeccion = calcular_inyeccion(inyeccion_std, plantas_yacimientos)
inyeccion_area = calcular_inyeccion_area(inyeccion, matriz_inyecciones)

inyeccion_yacimientos_areas = calcular_inyeccion_yacimientos_areas(yacimientos, plantas_yacimientos, inyeccion_area)
inyeccion_detalles_hubs = calcular_inyeccion_detalles_hubs(detalles_hubs, plantas_yacimientos)
inyeccion_flujos_directos = calcular_inyeccion_flujos_directos(flujos_directos, matriz_inyecciones)

guardar(inyeccion_flujos_directos, 'inyeccion_flujos_directos.csv')

tabla_total_yacimientos = calcular_tabla_total_yacimientos(inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area, premisas_areas, config.PERIODO_CONSIDERADO, COMPUESTOS)
tabla_total_flujos_directos = calcular_tabla_total_flujos_directos(inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas, config.PERIODO_CONSIDERADO, COMPUESTOS)
tabla_total_detalles_hubs = calcular_tabla_total_detalles_hubs(inyeccion_detalles_hubs, premisas_areas)

tabla_total_yacimientos = calcular_propiedades_gas(tabla_total_yacimientos, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)
tabla_total_flujos_directos = calcular_propiedades_gas(tabla_total_flujos_directos, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)
tabla_total_detalles_hubs = calcular_propiedades_gas(tabla_total_detalles_hubs, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)



guardar(tabla_total_yacimientos, 'TBL_TTL_YCS.csv')
guardar(tabla_total_flujos_directos, 'TBL_TTL_DTOS.csv')
guardar(tabla_total_detalles_hubs, 'TBL_TTL_DH.csv')



retenidos_TTY_DP = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'Dew point']
retenidos_TTY_TBX = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'TBX']
retenidos_MEGA = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'TBX MEGA']



#tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol, DERIVACION_TTY_DP, BYPASS_TTY_DP = modelar_TTY(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY=retenidos_TTY_DP, CAPACIDAD_TTY=config.CAPACIDAD_TTY_DP, CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_DP, MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_DP_A_MEGA)

#tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol, DERIVACION_TTY_TBX, BYPASS_TTY_TBX = modelar_TTY(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY=retenidos_TTY_TBX, CAPACIDAD_TTY=config.CAPACIDAD_TTY_TBX, CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_TBX, MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_TBX_A_TTY_DP)

MEGA = modelar_MEGA(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_MEGA = retenidos_MEGA, CAPACIDAD_MEGA=config.CAPACIDAD_MEGA, CAPACIDAD_EVACUACION_MEGA=config.CAPACIDAD_EVACUACION_MEGA)

TTY_DP = modelar_TTY(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY=retenidos_TTY_DP, CAPACIDAD_TTY=config.CAPACIDAD_TTY_DP, CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_DP)

TTY_TBX = modelar_TTY(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY=retenidos_TTY_TBX, CAPACIDAD_TTY=config.CAPACIDAD_TTY_TBX, CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_TBX)


tabla_mega = MEGA['tabla_total']

tabla_tty_dp = TTY_DP['tabla_total']

tabla_tty_tbx = TTY_TBX['tabla_total']




derivacion_TTY_TBX_a_TTY_DP = calcular_DERIVACION(tabla_planta_origen=TTY_TBX['tabla_total'], gas_rico_IN_origen=TTY_TBX['gas_rico_IN'], CAPACIDAD_EVACUACION_PLANTA=config.CAPACIDAD_EVACUACION_TTY_TBX, MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_TBX_A_TTY_DP)


tabla_tty_dp.loc[len(tabla_tty_dp)] = {'Volumen_inyectado' : derivacion_TTY_TBX_a_TTY_DP['vol_derivacion'],
                                   **dict(zip(COMPUESTOS, derivacion_TTY_TBX_a_TTY_DP['cromato_derivacion']))}


derivacion_TTY_DP_a_MEGA = calcular_DERIVACION(tabla_planta_origen=TTY_DP['tabla_total'], gas_rico_IN_origen=TTY_DP['gas_rico_IN'], CAPACIDAD_EVACUACION_PLANTA=config.CAPACIDAD_EVACUACION_TTY_DP, MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_DP_A_MEGA)




tabla_mega.loc[len(tabla_mega)] = {'Volumen_inyectado' : derivacion_TTY_DP_a_MEGA['vol_derivacion'],
                                   **dict(zip(COMPUESTOS, derivacion_TTY_DP_a_MEGA['cromato_derivacion']))}

MEGA = modelar_MEGA(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_MEGA = retenidos_MEGA, CAPACIDAD_MEGA=config.CAPACIDAD_MEGA, CAPACIDAD_EVACUACION_MEGA=config.CAPACIDAD_EVACUACION_MEGA)

TTY_DP = modelar_TTY(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY=retenidos_TTY_DP, CAPACIDAD_TTY=config.CAPACIDAD_TTY_DP, CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_DP)






red_gasoductos = pd.DataFrame(columns=["origen", "destino", "valor"])

red_gasoductos[["origen", "destino", "valor"]] = tabla_total_yacimientos[['Area', 'Gasoducto', 'Volumen_inyectado']]