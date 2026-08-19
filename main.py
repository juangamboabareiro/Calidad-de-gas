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


# La topologia de derivaciones es una CADENA sin ciclos:
#
#     TTY_TBX  --derivacion-->  TTY_DP  --derivacion-->  MEGA
#
# Por eso alcanza con modelar en ese orden, una sola pasada: cuando se modela
# una planta, la derivacion que recibe ya esta calculada. No hace falta iterar
# ni re-modelar nada. La derivacion se pasa a modelar_* y se inyecta como fila
# de input DENTRO de io_plantas (antes de Volumen_relativo), asi el gas derivado
# entra en la mezcla que forma gas_rico_IN.
#
# NOTA sobre el codigo anterior: se modelaba primero, se hacia
# tabla.loc[len(tabla)] = {...} sobre la tabla devuelta, y despues se volvia a
# llamar a modelar_*. Ese re-modelado reconstruye la tabla desde
# tabla_total_flujos_directos, entonces la fila agregada quedaba huerfana: la
# derivacion no impactaba en ningun resultado.


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
                      CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_TBX)


# 2) TTY-TBX -> TTY-DP
derivacion_TTY_TBX_a_TTY_DP = calcular_DERIVACION(
    tabla_planta_origen=TTY_TBX['tabla_total'],
    gas_rico_IN_origen=TTY_TBX['gas_rico_IN'],
    CAPACIDAD_EVACUACION_PLANTA=config.CAPACIDAD_EVACUACION_TTY_TBX,
    MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_TBX_A_TTY_DP,
    nombre_origen='tty_tbx')


TTY_DP = modelar_TTY(**comunes,
                     retenidos_TTY=retenidos_TTY_DP,
                     CAPACIDAD_TTY=config.CAPACIDAD_TTY_DP,
                     CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_DP,
                     derivaciones=[derivacion_TTY_TBX_a_TTY_DP])


# 3) TTY-DP -> MEGA
# gas_rico_IN de TTY-DP ya incluye el aporte de TBX, entonces la cromato que
# viaja a MEGA es la de la mezcla real. Con esto deja de hacer falta el
# IF(derivacion_TTY_DP_CROMA = 0, gas_rico_IN_TTY_TBX, ...) del Excel.
derivacion_TTY_DP_a_MEGA = calcular_DERIVACION(
    tabla_planta_origen=TTY_DP['tabla_total'],
    gas_rico_IN_origen=TTY_DP['gas_rico_IN'],
    CAPACIDAD_EVACUACION_PLANTA=config.CAPACIDAD_EVACUACION_TTY_DP,
    MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_DP_A_MEGA,
    nombre_origen='tty_dp')


MEGA = modelar_MEGA(**comunes,
                    retenidos_MEGA=retenidos_MEGA,
                    CAPACIDAD_MEGA=config.CAPACIDAD_MEGA,
                    CAPACIDAD_EVACUACION_MEGA=config.CAPACIDAD_EVACUACION_MEGA,
                    derivaciones=[derivacion_TTY_DP_a_MEGA])


tabla_tty_tbx = TTY_TBX['tabla_total']
tabla_tty_dp = TTY_DP['tabla_total']
tabla_mega = MEGA['tabla_total']


# TODO (signos): el volumen derivado se SUMA en la planta destino pero todavia
# no se RESTA en la planta origen, entonces
#   sum(tabla_tty_tbx) + sum(tabla_tty_dp) + sum(tabla_mega)
# ya no cierra contra la inyeccion total: el gas derivado se cuenta dos veces.
# Si hay que descontarlo, el lugar es un argumento vol_derivado_saliente en
# modelar_TTY que prorratee el descuento entre las filas del origen, calculado
# despues de la derivacion para mantener la pasada unica.
#
# TODO (bypass vs derivacion): hoy BYPASS se calcula sobre el volumen que llega
# (post-derivacion entrante) pero sin descontar lo que la planta deriva hacia
# afuera. Definir la prioridad: derivar primero y bypasear el resto, o al reves.

# endregion



red_gasoductos = pd.DataFrame(columns=["origen", "destino", "valor"])

red_gasoductos[["origen", "destino", "valor"]] = tabla_total_yacimientos[['Area', 'Gasoducto', 'Volumen_inyectado']]
