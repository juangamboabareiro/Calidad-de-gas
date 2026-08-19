# import pandas as pd
import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones, load_retenidos_rtp
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS, CONVERSION_BARRILLES_KGD
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


matriz_inyecciones = load_matriz_inyecciones(config.PATH_INPUTS)
retenidos_rtp = load_retenidos_rtp(config.PATH_INPUTS)








def correccion_TTY_TBX(retenidos_vol, PERIODO_CONSIDERADO, FECHA, CAPACIDAD, tabla_tty_tbx, propiedades, gas_rico_IN, retenidos):


    retenidos_vol = retenidos_vol/propiedades['Densidad Liquido [kg/m3]']

    etano_retenido = retenidos_vol['etano'] * CONVERSION_BARRILLES_KGD

    propano_retenido = retenidos_vol['propano'] * CONVERSION_BARRILLES_KGD

    butanos_retenido = retenidos_vol['butanos'] * CONVERSION_BARRILLES_KGD

    gasolina_retenido = retenidos_vol['gasolina'] * CONVERSION_BARRILLES_KGD

    correccion_gasolina = gasolina_retenido

    correccion_etano = etano_retenido


    ############################

    #BUTANOS CREO HAY ALGO RARO PORQUE NO SE SI ESTOY USANDO LA SUMA O LOS VALUES EN VERDAD

    ############################


    correccion_butanos = butanos_retenido.values if retenidos_vol.values.sum() < AUX else butanos_retenido.values * AUX/retenidos_vol.values.sum()

    correccion_propano = AUX - correccion_etano - correccion_butanos.values - correccion_gasolina if propano_retenido.values > 1 else 0

    

    coef_corr_propano = propano_retenido * 1000/(PRESION_BASE * min(CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[PROPANO] * gas_rico_IN.loc[PROPANO] * propiedades['Z'].loc[PROPANO] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE))

    coef_corr_butanos = (1000*retenidos.loc[BUTANOS]/butanos_retenido*correccion_butanos).values/(PRESION_BASE * min(CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[BUTANOS] * gas_rico_IN.fillna(0).loc[BUTANOS] * propiedades['Z'].loc[BUTANOS] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE)).values

    correcciones = pd.DataFrame({
        'etano' : correccion_etano,
        'propano' : correccion_propano,
        'butanos' : correccion_butanos,
        'gasolina' : correccion_gasolina
    })


    return correcciones, coef_corr_butanos, coef_corr_propano




def modelar_TTY_TBX(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_TTY_TBX):

    tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=retenidos_TTY_TBX,)

    if tabla_tty_tbx['Volumen_inyectado'].sum() > config.CAPACIDAD_TTY_TBX:

        correcciones, coef_corr_butanos, coef_corr_propano = correccion_TTY_TBX(tabla_tty_tbx=tabla_tty_tbx, retenidos_vol=retenidos_vol, PERIODO_CONSIDERADO=config.PERIODO_CONSIDERADO, FECHA = config.FECHA_PM_TTY_TBX, propiedades=propiedades, CAPACIDAD=config.CAPACIDAD, gas_rico_IN=gas_rico_IN, retenidos=retenidos)

        new_retenidos = retenidos_TTY_TBX.T

        new_retenidos.loc[PROPANO] = coef_corr_propano.loc[PROPANO].fillna(0)



        for i in range(len(BUTANOS)):
            new_retenidos.loc[BUTANOS[i]] = np.ravel(coef_corr_butanos)[i]
    

        tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=new_retenidos.T)

        


    return tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol







