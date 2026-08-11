# import pandas as pd
import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones, load_retenidos_rtp
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS, CONVERSION_BARRILLES_KGD
from config import CAPACIDAD, FECHA_RANDOM, PERIODO_CONSIDERADO, CAPACIDAD_TTY_TBX, CAPACIDAD_ADICIONAL_TBX, CAPACIDAD_BASE_CONVERTIBLE_TBX, CAPACIDAD_MEGA, PATH_INPUTS
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


matriz_inyecciones = load_matriz_inyecciones(PATH_INPUTS)
retenidos_rtp = load_retenidos_rtp(PATH_INPUTS)


def modelar_MEGA(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_MEGA):

    tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=retenidos_MEGA,)


    if retenidos_vol.values.sum()/1000 > CAPACIDAD_MEGA:

        coef_correccion = CAPACIDAD_MEGA/(retenidos_vol.values.sum()/1000)


        retenidos_vol = retenidos_vol * coef_correccion

        return tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol

    else:

        return tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol