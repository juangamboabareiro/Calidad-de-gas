import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones, load_retenidos_rtp
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


matriz_inyecciones = load_matriz_inyecciones(config.PATH_INPUTS)
retenidos_rtp = load_retenidos_rtp(config.PATH_INPUTS)


# tbx = tabla_tty_tbx['Volumen inyectado']/tabla_tty_tbx['Volumen inyectado'].sum() * FLUJO_SIN_BYPASS_TBX 

# dp = max(min(tabla_tty_dp['Volumen inyectado']/tabla_tty_dp['Volumen inyectado'].sum() * CAPACIDAD, tabla_tty_tbx['Volumen inyectado'] - tbx), 0)

# FLUJO_SIN_BYPASS = min(tabla_tty_dp['Volumen inyectado'].sum(), dp.sum())

# BYPASS = min(tabla_tty_dp['Volumen inyectado'].sum() - )


def correccion_TTY_DP(retenidos_vol, tabla_tty_dp, propiedades, gas_rico_IN, retenidos, CAPACIDAD_EVACUACION_TTY_DP):

    etano_retenido = retenidos_vol['etano']

    propano_retenido = retenidos_vol['propano']

    butanos_retenido = retenidos_vol['butanos']

    gasolina_retenido = retenidos_vol['gasolina']


    #GASOLINA PASA 100%
    correccion_gasolina = gasolina_retenido

    #NO TRATA ETANO
    correccion_etano = etano_retenido

    #PROPORCIONAL HASTA 200 TN/D PRIMERO C4[BUTANOS] Y DSP C3[PROPANO]
    correccion_butanos = CAPACIDAD_EVACUACION_TTY_DP if butanos_retenido.values > CAPACIDAD_EVACUACION_TTY_DP else butanos_retenido

    correccion_propano = min(max(CAPACIDAD_EVACUACION_TTY_DP - butanos_retenido.values, 0), propano_retenido.values)

    

    coef_corr_propano = propano_retenido * 1000/(PRESION_BASE * min(CAPACIDAD_EVACUACION_TTY_DP, tabla_tty_dp['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[PROPANO] * gas_rico_IN.loc[PROPANO] * propiedades['Z'].loc[PROPANO] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE))

    coef_corr_butanos = (1000*retenidos.loc[BUTANOS]/butanos_retenido*correccion_butanos).values/(PRESION_BASE * min(CAPACIDAD_EVACUACION_TTY_DP, tabla_tty_dp['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[BUTANOS] * gas_rico_IN.fillna(0).loc[BUTANOS] * propiedades['Z'].loc[BUTANOS] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE)).values

    correcciones = pd.DataFrame({
        'etano' : correccion_etano,
        'propano' : correccion_propano,
        'butanos' : correccion_butanos,
        'gasolina' : correccion_gasolina
    })


    return correcciones, coef_corr_butanos, coef_corr_propano




def modelar_TTY_DP(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_TTY_DP, CAPACIDAD_TTY_DP, CAPACIDAD_EVACUACION_TTY_DP_TTY_DP, MAX_DERIVACION_TTY_DP_A_MEGA):

    tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, compuestos=COMPUESTOS, retenidos_planta=retenidos_TTY_DP, nombre_planta='TTY')



    DERIVACION_A_MEGA = min(max(tabla_tty_dp['Volumen_inyectado'].values.sum() - CAPACIDAD_EVACUACION_TTY_DP_TTY_DP, 0), MAX_DERIVACION_TTY_DP_A_MEGA)

    BYPASS_TTY_DP = tabla_tty_dp['Volumen_inyectado'].values.sum() - CAPACIDAD_EVACUACION_TTY_DP_TTY_DP - DERIVACION_A_MEGA + max(tabla_tty_dp['Volumen_inyectado'].values.sum() - CAPACIDAD_TTY_DP, 0)



    if retenidos_vol.values.sum() > CAPACIDAD_EVACUACION_TTY_DP_TTY_DP:

        correcciones, coef_corr_butanos, coef_corr_propano = correccion_TTY_DP(tabla_tty_dp=tabla_tty_dp, retenidos_vol=retenidos_vol, propiedades=propiedades, gas_rico_IN=gas_rico_IN, retenidos=retenidos, CAPACIDAD_EVACUACION_TTY_DP_TTY_DP=CAPACIDAD_EVACUACION_TTY_DP_TTY_DP)

        new_retenidos = retenidos_TTY_DP.T

        new_retenidos.loc[PROPANO] = coef_corr_propano.loc[PROPANO].fillna(0)



        for i in range(len(BUTANOS)):
            new_retenidos.loc[BUTANOS[i]] = np.ravel(coef_corr_butanos)[i]
    

        tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=new_retenidos.T)

        


    return tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol, DERIVACION_A_MEGA, BYPASS_TTY_DP







