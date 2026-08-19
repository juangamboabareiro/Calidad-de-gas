import pandas as pd
import numpy as np
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas
from pipeline.plantas.flujo_plantas import calcular_BYPASS, calcular_DERIVACION





def correccion_TTY(retenidos_vol, tabla_tty, propiedades, gas_rico_IN, retenidos, CAPACIDAD_EVACUACION_TTY):

    etano_retenido = retenidos_vol['etano']

    propano_retenido = retenidos_vol['propano']

    butanos_retenido = retenidos_vol['butanos']

    gasolina_retenido = retenidos_vol['gasolina']


    #GASOLINA PASA 100%
    correccion_gasolina = gasolina_retenido

    #NO TRATA ETANO
    correccion_etano = etano_retenido

    #PROPORCIONAL HASTA 200 TN/D PRIMERO C4[BUTANOS] Y DSP C3[PROPANO]
    correccion_butanos = CAPACIDAD_EVACUACION_TTY if butanos_retenido.values > CAPACIDAD_EVACUACION_TTY else butanos_retenido

    correccion_propano = min(max(CAPACIDAD_EVACUACION_TTY - butanos_retenido.values, 0), propano_retenido.values)



    coef_corr_propano = propano_retenido /(PRESION_BASE * min(CAPACIDAD_EVACUACION_TTY, tabla_tty['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[PROPANO] * gas_rico_IN.loc[PROPANO] * propiedades['Z'].loc[PROPANO] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE))

    coef_corr_butanos = (retenidos.loc[BUTANOS]/butanos_retenido*correccion_butanos).values/(PRESION_BASE * min(CAPACIDAD_EVACUACION_TTY, tabla_tty['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[BUTANOS] * gas_rico_IN.fillna(0).loc[BUTANOS] * propiedades['Z'].loc[BUTANOS] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE)).values

    correcciones = pd.DataFrame({
        'etano' : correccion_etano,
        'propano' : correccion_propano,
        'butanos' : correccion_butanos,
        'gasolina' : correccion_gasolina
    })


    return correcciones, coef_corr_butanos, coef_corr_propano




def modelar_TTY(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_TTY, CAPACIDAD_TTY, CAPACIDAD_EVACUACION_TTY, derivaciones=None):
    """Modela una planta TTY (Dew Point o TBX).

    derivaciones : list[dict] | None
        Derivaciones que ENTRAN a esta planta desde otra, tal como las devuelve
        calcular_DERIVACION. Se inyectan dentro de io_plantas ANTES de calcular
        Volumen_relativo, así el gas derivado pesa en la mezcla de gas_rico_IN.
        Si no se pasan, la planta se modela solo con su gas propio.
    """

    # Se arman los kwargs una sola vez para garantizar que el re-modelado por
    # correccion de LGN (abajo) use EXACTAMENTE las mismas derivaciones. Si se
    # pasan solo en la primera llamada, la derivacion se pierde en silencio
    # cada vez que se dispara la correccion.
    comunes = dict(
        matriz_inyecciones = matriz_inyecciones,
        calcular_retenidos=calcular_retenidos,
        tabla_total_flujos_directos=tabla_total_flujos_directos,
        propiedades=propiedades,
        compuestos=COMPUESTOS,
        nombre_planta='TTY',
        derivaciones=derivaciones,
    )

    tabla_tty, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(**comunes, retenidos_planta=retenidos_TTY)


    # OJO: tabla_tty ya incluye las filas de derivacion entrante, entonces el
    # BYPASS se calcula sobre el volumen POST-derivacion (gas que efectivamente
    # llega a la planta). Lo que esta planta deriva hacia otra todavia NO se
    # descuenta de su propio volumen -> ver TODO de signos en main.py.
    BYPASS_TTY = calcular_BYPASS(tabla_planta=tabla_tty, CAPACIDAD_EVACUACION_PLANTA=CAPACIDAD_EVACUACION_TTY, CAPACIDAD_PLANTA=CAPACIDAD_TTY)

    if retenidos_vol.values.sum() > CAPACIDAD_EVACUACION_TTY:

        correcciones, coef_corr_butanos, coef_corr_propano = correccion_TTY(tabla_tty=tabla_tty, retenidos_vol=retenidos_vol, propiedades=propiedades, gas_rico_IN=gas_rico_IN, retenidos=retenidos, CAPACIDAD_EVACUACION_TTY=CAPACIDAD_EVACUACION_TTY)

        new_retenidos = retenidos_TTY.T

        new_retenidos.loc[PROPANO] = coef_corr_propano.loc[PROPANO].fillna(0)



        for i in range(len(BUTANOS)):
            new_retenidos.loc[BUTANOS[i]] = np.ravel(coef_corr_butanos)[i]


        tabla_tty, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(**comunes, retenidos_planta=new_retenidos.T)

    return {'tabla_total' : tabla_tty, 'gas_rico_IN' : gas_rico_IN, 'gas_residual_OUT' : gas_residual_OUT, 'retenidos' : retenidos, 'retenidos_vol' : retenidos_vol, 'bypass' : BYPASS_TTY}
