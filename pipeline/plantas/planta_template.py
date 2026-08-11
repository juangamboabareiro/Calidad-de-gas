import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS
from config import CAPACIDAD, FECHA_RANDOM, PERIODO_CONSIDERADO, PATH_INPUTS
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos


matriz_inyecciones = load_matriz_inyecciones(PATH_INPUTS)


def io_plantas(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_planta):

    tabla_plantas = pd.DataFrame()
    tabla_plantas['Area'] = matriz_inyecciones['TTY']
    tabla_plantas['Area'] = tabla_plantas['Area'].apply(normalizar)


    tabla_plantas = tabla_plantas.merge(
        tabla_total_flujos_directos,
        on='Area',
        how='inner'
    )


    tabla_plantas['Volumen_relativo'] = tabla_plantas['Volumen_inyectado']/(tabla_plantas['Volumen_inyectado'].sum())

    tabla_plantas = tabla_plantas.fillna(0)

    gas_rico_IN = tabla_plantas[COMPUESTOS].T.dot(tabla_plantas['Volumen_relativo'])





    gas_residual_OUT = gas_rico_IN * (1 - retenidos_planta)

    retenidos = calcular_retenidos(propiedades, tabla_plantas['Volumen_inyectado'].sum(), retenidos_planta, gas_rico_IN, PRESION_BASE, CONSTANTE_GAS, TEMPERATURA_BASE).T



    etano_retenido = retenidos.loc[ETANO].sum()
    propano_retenido = retenidos.loc[PROPANO].sum()
    butanos_retenido = retenidos.loc[BUTANOS].sum()
    gasolina_retenido = retenidos.loc[GASOLINA].sum()


    retenidos_vol = pd.DataFrame({
        'etano' : etano_retenido,
        'propano' : propano_retenido,
        'butanos' : butanos_retenido,
        'gasolina' : gasolina_retenido
    })


    return tabla_plantas, gas_rico_IN, gas_residual_OUT, retenidos, retenidos_vol
