# import pandas as pd
import pandas as pd
import numpy as np
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS, CONVERSION_BARRILLES_KGD
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


from pipeline.plantas.flujo_plantas import calcular_DERIVACION, calcular_BYPASS



def modelar_MEGA(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_MEGA, CAPACIDAD_MEGA, CAPACIDAD_EVACUACION_MEGA, derivaciones=None):
    """Modela MEGA.

    derivaciones : list[dict] | None
        Derivaciones que ENTRAN a MEGA (tipicamente desde TTY-DP), tal como las
        devuelve calcular_DERIVACION. Se inyectan dentro de io_plantas antes de
        calcular Volumen_relativo, asi el gas derivado entra en la mezcla de
        gas_rico_IN en vez de quedar colgado en una fila que nadie lee.
    """

    tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(matriz_inyecciones = matriz_inyecciones, calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, compuestos=COMPUESTOS, retenidos_planta=retenidos_MEGA, nombre_planta='MEGA', derivaciones=derivaciones)

    BYPASS_MEGA = calcular_BYPASS(tabla_mega, CAPACIDAD_EVACUACION_PLANTA=CAPACIDAD_EVACUACION_MEGA, CAPACIDAD_PLANTA=CAPACIDAD_MEGA)

    if retenidos_vol.values.sum()/1000 > CAPACIDAD_EVACUACION_MEGA:

        coef_correccion = CAPACIDAD_EVACUACION_MEGA/(retenidos_vol.values.sum()/1000)

        retenidos_vol = retenidos_vol * coef_correccion

    return {'tabla_total' : tabla_mega, 'gas_rico_IN' : gas_rico_IN, 'gas_residual_OUT' : gas_residual_OUT, 'retenidos' : retenidos, 'retenidos_vol' : retenidos_vol, 'bypass' : BYPASS_MEGA}
