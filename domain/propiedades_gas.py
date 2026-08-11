import pandas as pd
import numpy as np


def calcular_propiedades_gas(
        df: pd.DataFrame, 
        df2: pd.DataFrame, 
        lista_de_cols, 
        PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS,DENSIDAD_AIRE,CONVERSION) -> pd.DataFrame:
    """
    Calcula las df2 físicas del gas
    a partir de su composición molar.

    Las df2 calculadas son:

    - Factor de compresibilidad (z)
    - Densidad relativa al aire
    - Poder Calorífico Superior (PCS)
    - Índice de Wobbe (IW)

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame que contiene las composiciones molares de las corrientes.

    lista_de_cols : list[str]
        Lista de columnas correspondientes a los componentes de la mezcla
        gaseosa (C1, C2, C3, CO2, N2, etc.).

    Returns
    -------
    None
        La función agrega y actualiza las columnas 'z', 'densidad',
        'PCS' e 'IW' directamente en el DataFrame recibido.

    Notes
    -----
    Los cálculos utilizan las df2 individuales de cada componente
    almacenadas en la tabla 'df2' y las constantes operativas
    definidas en 'constantes_GAS'.
    """
    res = df
    
    z = 1 - (PRESION_BASE) * ((df[lista_de_cols].dot(df2['Factor b']))**2)

    res['z'] = z

    densidad = df[lista_de_cols].dot(df2['Peso molecular [kg/kmol]'])

    densidad = PRESION_BASE * densidad / z / (273.15 + TEMPERATURA_BASE) / CONSTANTE_GAS / DENSIDAD_AIRE

    res['densidad'] = densidad


    PCS = df[lista_de_cols].dot(df2['PCS [kJ/mol]'])

    PCS = PRESION_BASE * PCS / z / (273.15 + TEMPERATURA_BASE) / CONSTANTE_GAS / CONVERSION * 1000

    res['PCS'] = PCS


    IW = (PCS/np.sqrt(densidad)).fillna(0)

    res['IW'] = IW

    


    return res



def calcular_retenidos(propiedades, volumen_total, retenido, gas_rico_in, PRESION_BASE, CONSTANTE_GAS, TEMPERATURA_BASE):

    num = PRESION_BASE * volumen_total * propiedades['Peso molecular [kg/kmol]'] * gas_rico_in * retenido
    denom = propiedades['Z'] * CONSTANTE_GAS * (273.15 + TEMPERATURA_BASE)

    return (num/denom)



def calcular_energia_total(propiedades, volumen_total, gas_rico_in, PRESION_BASE, CONSTANTE_GAS, TEMPERATURA_BASE, MMBtu):

    num = PRESION_BASE * volumen_total * propiedades['Peso molecular [kg/kmol]'] * gas_rico_in
    denom = propiedades['Z'] * CONSTANTE_GAS * (273.15 + TEMPERATURA_BASE) * propiedades['PCS [kJ/mol]'] / MMBtu

    return (num/denom)