# import pandas as pd
import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones, load_retenidos_rtp
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS, CONVERSION_BARRILLES_KGD
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


from pipeline.plantas.flujo_plantas import calcular_DERIVACION, calcular_BYPASS


matriz_inyecciones = load_matriz_inyecciones(config.PATH_INPUTS)
retenidos_rtp = load_retenidos_rtp(config.PATH_INPUTS)


def modelar_MEGA(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_MEGA, CAPACIDAD_MEGA, CAPACIDAD_EVACUACION_MEGA):

    tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, compuestos=COMPUESTOS, retenidos_planta=retenidos_MEGA, nombre_planta='MEGA')

    BYPASS_MEGA = calcular_BYPASS(tabla_mega, CAPACIDAD_EVACUACION_PLANTA=CAPACIDAD_EVACUACION_MEGA, CAPACIDAD_PLANTA=CAPACIDAD_MEGA)

    if retenidos_vol.values.sum()/1000 > CAPACIDAD_EVACUACION_MEGA:

        coef_correccion = CAPACIDAD_EVACUACION_MEGA/(retenidos_vol.values.sum()/1000)

        retenidos_vol = retenidos_vol * coef_correccion

    return {'tabla_total' : tabla_mega, 'gas_rico_IN' : gas_rico_IN, 'gas_residual_OUT' : gas_residual_OUT, 'retenidos' : retenidos, 'retenidos_vol' : retenidos_vol, 'bypass' : BYPASS_MEGA}


    