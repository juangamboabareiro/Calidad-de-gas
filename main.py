# region MODULOS

import pandas as pd
import numpy as np

from domain.ctes_gas import *
from pipeline.preprocesamiento import preprocesar_inputs
from config import CAPACIDAD, PERIODO_CONSIDERADO, FECHA_RANDOM, PATH_INPUTS, CAPACIDAD_MEGA
from pipeline.inyeccion_std import calcular_inyeccion_std
from io_.loaders import load_inyeccion_9300, load_coeficientes, load_retenidos_rtp
from pipeline.inyeccion_area import calcular_inyeccion_area, calcular_inyeccion
from pipeline.yacimientos import calcular_inyeccion_yacimientos_areas
from pipeline.detalles_hubs import calcular_inyeccion_detalles_hubs
from pipeline.flujos_directos import calcular_inyeccion_flujos_directos
from pipeline.tabla_total import calcular_tabla_total_yacimientos, calcular_tabla_total_flujos_directos, calcular_tabla_total_detalles_hubs
from domain.propiedades_gas import calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.TTY_DP import modelar_TTY_DP
from pipeline.plantas.TTY_TBX import modelar_TTY_TBX
from pipeline.plantas.MEGA import modelar_MEGA
from outputs.writers import guardar
from io_.loaders import load_flujos_directos, load_yacimientos, load_detalles_hubs, load_propiedades, load_plantas_yacimientos, load_matriz_inyecciones, load_premisas_areas, load_coefs_inyeccion_area, load_retenidos_rtp




# endregion



# region inputs

inyeccion_9300 = load_inyeccion_9300(PATH_INPUTS)
coeficientes = load_coeficientes(PATH_INPUTS)
retenidos_RTP = load_retenidos_rtp(PATH_INPUTS)

flujos_directos = load_flujos_directos(PATH_INPUTS)
yacimientos = load_yacimientos(PATH_INPUTS)
detalles_hubs = load_detalles_hubs(PATH_INPUTS)
propiedades = load_propiedades(PATH_INPUTS)
plantas_yacimientos = load_plantas_yacimientos(PATH_INPUTS)


#endregion



# region preprocesamiento de datos

flujos_directos, yacimientos, detalles_hubs, propiedades, plantas_yacimientos, matriz_inyecciones, coefs_inyeccion_area, premisas_areas = preprocesar_inputs(flujos_directos=flujos_directos, yacimientos=yacimientos, detalles_hubs=detalles_hubs, propiedades=propiedades, plantas_yacimientos=plantas_yacimientos)

# endregion




inyeccion_std = calcular_inyeccion_std(inyeccion_9300, coeficientes)
inyeccion = calcular_inyeccion(inyeccion_std, plantas_yacimientos)
inyeccion_area = calcular_inyeccion_area(inyeccion, matriz_inyecciones)

inyeccion_yacimientos_areas = calcular_inyeccion_yacimientos_areas(yacimientos, plantas_yacimientos, inyeccion_area)
inyeccion_detalles_hubs = calcular_inyeccion_detalles_hubs(detalles_hubs, plantas_yacimientos)
inyeccion_flujos_directos = calcular_inyeccion_flujos_directos(flujos_directos, matriz_inyecciones)

tabla_total_yacimientos = calcular_tabla_total_yacimientos(inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area, premisas_areas, PERIODO_CONSIDERADO, COMPUESTOS)
tabla_total_flujos_directos = calcular_tabla_total_flujos_directos(inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas, PERIODO_CONSIDERADO, COMPUESTOS)
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


tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = modelar_TTY_DP(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY_DP=retenidos_TTY_DP)


tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = modelar_TTY_TBX(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY_TBX=retenidos_TTY_TBX)


tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = modelar_MEGA(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_MEGA = retenidos_MEGA)



#print(tabla_total_yacimientos[['Area', 'Gasoducto', 'Volumen_inyectado']])

#volumen_dp = max(min(tabla_tty_dp['Volumen_inyectado']/tabla_tty_dp['Volumen_inyectado'].values.sum()*CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'] - (tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * CAPACIDAD)),0)


#volumen_bypass = min(volumen_dp, 5)

# tabla_tty_dp['Volumen_inyectado'] = tabla_tty_dp['Volumen_inyectado']/1000000
# tabla_tty_tbx['Volumen_inyectado'] = tabla_tty_tbx['Volumen_inyectado']/1000000

# print(tabla_tty_dp['Volumen_inyectado']/tabla_tty_dp['Volumen_inyectado'].values.sum()*CAPACIDAD)
# print(tabla_tty_tbx['Volumen_inyectado'] - (tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * CAPACIDAD))

# volumen_dp = np.maximum(np.minimum(tabla_tty_dp['Volumen_inyectado']/tabla_tty_dp['Volumen_inyectado'].values.sum()*CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'] - (tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * CAPACIDAD)), 0)

# volumen_tbx = tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * np.minimum(tabla_tty_tbx['Volumen_inyectado'],CAPACIDAD)

# volumen_tbx_mega = np.minimum(tabla_mega['Volumen_inyectado']/(tabla_mega['Volumen_inyectado'].values.sum()) * CAPACIDAD_MEGA, tabla_mega['Volumen_inyectado'])

# #volumen_tbx_mega_aj = volumen_tbx_mega*coef_correccion

# print(volumen_dp)

# print(volumen_tbx)

# print(volumen_tbx_mega)




# volumen_bypass_tty_dp = max(tabla_tty_dp['Volumen_inyectado'].values.sum() - min(volumen_dp, tabla_tty_dp['Volumen_inyectado'].values.sum()) - min(tabla_tty_tbx['Volumen_inyectado'].values.sum(), CAPACIDAD_TTY_TBX))
# volumen_bypass_mega = min(volumen_bypass_tty_dp,5)
# volumen_bypass_tty_tbx = 0


# bypass_tty_dp_molar = gas_rico_IN_tty_tbx if gas_rico_IN_tty_dp == 0 else gas_rico_IN_tty_dp

# #print(min(tabla_tty_dp['Volumen_inyectado']/tabla_tty_dp['Volumen_inyectado'].values.sum()*CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'] - (tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * CAPACIDAD)))