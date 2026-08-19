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
from pipeline.plantas.flujo_plantas import calcular_flujos_planta, calcular_DERIVACION




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



# region tablas totales

inyeccion_std = calcular_inyeccion_std(inyeccion_9300, coeficientes)
inyeccion = calcular_inyeccion(inyeccion_std, plantas_yacimientos)
inyeccion_area = calcular_inyeccion_area(inyeccion, matriz_inyecciones)

inyeccion_yacimientos_areas = calcular_inyeccion_yacimientos_areas(yacimientos, plantas_yacimientos, inyeccion_area)
inyeccion_detalles_hubs = calcular_inyeccion_detalles_hubs(detalles_hubs, plantas_yacimientos)
inyeccion_flujos_directos = calcular_inyeccion_flujos_directos(flujos_directos, matriz_inyecciones)

tabla_total_yacimientos = calcular_tabla_total_yacimientos(inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area, premisas_areas, config.PERIODO_CONSIDERADO, COMPUESTOS)
tabla_total_flujos_directos = calcular_tabla_total_flujos_directos(inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas, config.PERIODO_CONSIDERADO, COMPUESTOS)
tabla_total_detalles_hubs = calcular_tabla_total_detalles_hubs(inyeccion_detalles_hubs, premisas_areas)

tabla_total_yacimientos = calcular_propiedades_gas(tabla_total_yacimientos, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)
tabla_total_flujos_directos = calcular_propiedades_gas(tabla_total_flujos_directos, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)
tabla_total_detalles_hubs = calcular_propiedades_gas(tabla_total_detalles_hubs, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)



guardar(tabla_total_yacimientos, 'TBL_TTL_YCS.csv')
guardar(tabla_total_flujos_directos, 'TBL_TTL_DTOS.csv')
guardar(tabla_total_detalles_hubs, 'TBL_TTL_DH.csv')

# endregion



# region modelado de plantas + derivaciones

retenidos_TTY_DP = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'Dew point']
retenidos_TTY_TBX = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'TBX']
retenidos_MEGA = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'TBX MEGA']


# Lo que limita no es el ingreso de gas (holgado) sino la EVACUACION DE LGN.
# Y como el gas hay que tratarlo lo mas posible para comercializarlo, en cada
# planta el orden es: corregir recuperacion -> derivar el excedente -> bypasear
# solo lo que ni derivando entra. Ver calcular_flujos_planta.
#
# La topologia de derivaciones es una CADENA sin ciclos:
#
#     TTY_TBX  --derivacion-->  TTY_DP  --derivacion-->  MEGA (no deriva)
#
# Por eso alcanza con modelar en ese orden, una sola pasada: cuando se modela
# una planta, la derivacion que recibe ya esta calculada. La derivacion se pasa
# a modelar_* y se inyecta como fila de input DENTRO de io_plantas (antes de
# Volumen_relativo), asi el gas derivado entra en la mezcla de gas_rico_IN.


comunes = dict(
    matriz_inyecciones = load_matriz_inyecciones(config.PATH_INPUTS),
    calcular_retenidos=calcular_retenidos,
    tabla_total_flujos_directos=tabla_total_flujos_directos,
    propiedades=propiedades,
    COMPUESTOS=COMPUESTOS,
)


# 1) TTY-TBX: cabeza de la cadena, no recibe derivaciones.
TTY_TBX = modelar_TTY(**comunes,
                      retenidos_TTY=retenidos_TTY_TBX,
                      CAPACIDAD_TTY=config.CAPACIDAD_TTY_TBX,
                      CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_TBX,
                      MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_TBX_A_TTY_DP,
                      factor_retenidos=config.FACTOR_RETENIDOS_TTY_TBX)


# 2) TTY-TBX -> TTY-DP
derivacion_TTY_TBX_a_TTY_DP = calcular_DERIVACION(
    flujos_origen=TTY_TBX['flujos'],
    gas_rico_IN_origen=TTY_TBX['gas_rico_IN'],
    nombre_origen='tty_tbx')


TTY_DP = modelar_TTY(**comunes,
                     retenidos_TTY=retenidos_TTY_DP,
                     CAPACIDAD_TTY=config.CAPACIDAD_TTY_DP,
                     CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_DP,
                     derivaciones=[derivacion_TTY_TBX_a_TTY_DP],
                     MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_DP_A_MEGA,
                     factor_retenidos=config.FACTOR_RETENIDOS_TTY_DP)


# 3) TTY-DP -> MEGA
# gas_rico_IN de TTY-DP ya incluye el aporte de TBX, entonces la cromato que
# viaja a MEGA es la de la mezcla real. Con esto deja de hacer falta el
# IF(derivacion_TTY_DP_CROMA = 0, gas_rico_IN_TTY_TBX, ...) del Excel.
derivacion_TTY_DP_a_MEGA = calcular_DERIVACION(
    flujos_origen=TTY_DP['flujos'],
    gas_rico_IN_origen=TTY_DP['gas_rico_IN'],
    nombre_origen='tty_dp')


# MEGA: sin poder de derivacion, todo su excedente es bypass.
MEGA = modelar_MEGA(**comunes,
                    retenidos_MEGA=retenidos_MEGA,
                    CAPACIDAD_MEGA=config.CAPACIDAD_MEGA,
                    CAPACIDAD_EVACUACION_MEGA=config.CAPACIDAD_EVACUACION_MEGA,
                    derivaciones=[derivacion_TTY_DP_a_MEGA],
                    factor_retenidos=config.FACTOR_RETENIDOS_MEGA)


tabla_tty_tbx = TTY_TBX['tabla_total']
tabla_tty_dp = TTY_DP['tabla_total']
tabla_mega = MEGA['tabla_total']

print(tabla_mega[['Area', 'Volumen']])

flujos_plantas = pd.DataFrame({
    'TTY_TBX': TTY_TBX['flujos'],
    'TTY_DP': TTY_DP['flujos'],
    'MEGA': MEGA['flujos'],
}).T[['vol_entrante', 'vol_procesado', 'vol_derivado', 'bypass', 'excedente', 'lgn_potencial', 'fraccion_tratable']]


# Balance por planta: vol_entrante == vol_procesado + vol_derivado + bypass, y
# el vol_derivado de una aparece dentro del vol_entrante de la siguiente.
# OJO: sum(tabla_*) SI se solapa entre plantas, porque la tabla del destino
# incluye la fila de derivacion. Para totalizar volumen usar flujos_plantas;
# las tabla_* sirven para composiciones.
_chequeo_balance = (
    flujos_plantas['vol_entrante']
    - flujos_plantas[['vol_procesado', 'vol_derivado', 'bypass']].sum(axis=1)
).abs().max()

assert _chequeo_balance < 1e-6, f'El balance por planta no cierra: {_chequeo_balance}'

# endregion



red_gasoductos = pd.DataFrame(columns=["origen", "destino", "valor"])

red_gasoductos[["origen", "destino", "valor"]] = tabla_total_yacimientos[['Area', 'Gasoducto', 'Volumen_inyectado']]
