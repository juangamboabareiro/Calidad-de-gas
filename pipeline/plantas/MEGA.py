# import pandas as pd
import pandas as pd
import numpy as np
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS, CONVERSION_BARRILLES_KGD
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


from pipeline.plantas.flujo_plantas import (
    calcular_lgn_unitario,
    calcular_volumen_maximo,
    repartir_flujo_planta,
    calcular_DERIVACION,
)



def modelar_MEGA(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_MEGA, CAPACIDAD_EVACUACION_MEGA, CAPACIDAD_MEGA=None, derivaciones=None, tabla_total_yacimientos=None, tabla_total_hubs=None, mapa_area_hub=None):
    """Modela MEGA: ultimo eslabon de la cascada.

    MEGA tiene pool propio y ademas recibe la derivacion de TTY-DP, que viene
    con OTRA composicion (el gas del pool TTY). Por eso si se pasa por
    derivaciones=[...] a io_plantas, que la suma como fila de input antes de
    calcular Volumen_relativo y la mete en la mezcla de gas_rico_IN. Recien con
    la mezcla armada tiene sentido calcular el LGN por unidad de volumen.

    No deriva hacia ningun lado (MAX_DERIVACION fijo en 0), entonces todo lo que
    no pueda tratar es BYPASS.
    """

    tabla_pool, gas_rico_IN, gas_residual_OUT, retenidos_pool, retenidos_vol_pool = io_plantas(
        matriz_inyecciones=matriz_inyecciones,
        calcular_retenidos=calcular_retenidos,
        tabla_total_flujos_directos=tabla_total_flujos_directos,
        tabla_total_yacimientos=tabla_total_yacimientos,   # nuevo
        tabla_total_hubs=tabla_total_hubs,                 # gas via HUB
        mapa_area_hub=mapa_area_hub,
        propiedades=propiedades,
        compuestos=COMPUESTOS,
        retenidos_planta=retenidos_MEGA,
        nombre_planta='MEGA',
        derivaciones=derivaciones,
    )

    vol_pool = float(tabla_pool['Volumen_inyectado'].values.sum())

    lgn_unitario = calcular_lgn_unitario(vol_pool, retenidos_vol_pool)

    vol_maximo = calcular_volumen_maximo(
        lgn_unitario=lgn_unitario,
        CAPACIDAD_EVACUACION_PLANTA=CAPACIDAD_EVACUACION_MEGA,
        CAPACIDAD_INGRESO_PLANTA=CAPACIDAD_MEGA,
    )

    # vol_disponible = pool propio + derivacion recibida (ya viene sumada en la
    # tabla). MAX_DERIVACION = 0: ultimo eslabon, el sobrante es todo bypass.
    flujos = repartir_flujo_planta(
        vol_disponible=vol_pool,
        vol_maximo=vol_maximo,
        MAX_DERIVACION_PLANTA_A_PLANTA=0.0,
    )

    flujos['lgn_unitario'] = lgn_unitario
    flujos['lgn_asignado'] = lgn_unitario * flujos['vol_asignado']
    flujos['activa'] = True

    escala = (flujos['vol_asignado'] / vol_pool) if vol_pool else 0.0

    tabla_mega = tabla_pool.copy()
    tabla_mega['Volumen_pool'] = tabla_pool['Volumen_inyectado']
    tabla_mega['Volumen_inyectado'] = tabla_pool['Volumen_inyectado'] * escala

    retenidos = retenidos_pool * escala
    retenidos_vol = retenidos_vol_pool * escala

    return {
        'tabla_total': tabla_mega,
        'gas_rico_IN': gas_rico_IN,
        'gas_residual_OUT': gas_residual_OUT,
        'retenidos': retenidos,
        'retenidos_vol': retenidos_vol,
        'flujos': flujos,
        'bypass': flujos['bypass'],
    }
