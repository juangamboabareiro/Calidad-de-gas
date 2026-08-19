import pandas as pd
import numpy as np
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas
from pipeline.plantas.flujo_plantas import calcular_flujos_planta, calcular_DERIVACION





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




def modelar_TTY(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_TTY, CAPACIDAD_TTY, CAPACIDAD_EVACUACION_TTY, derivaciones=None, MAX_DERIVACION_PLANTA_A_PLANTA=0.0, factor_retenidos=1.0, capacidad_libre_destino=None):
    """Modela una planta TTY (Dew Point o TBX).

    ORDEN DE RESOLUCION (lo que limita es la evacuacion de LGN, no el ingreso
    de gas: la capacidad de ingreso es holgada y el gas hay que tratarlo lo mas
    posible para comercializarlo):

        1. modelar con los coeficientes de retencion nominales
        2. si el LGN potencial supera la evacuacion -> CORREGIR la recuperacion
           (menos C3/C4) para tratar todo lo que se pueda
        3. si aun corregida sigue sin entrar -> DERIVAR el excedente a la
           planta siguiente, hasta MAX_DERIVACION_PLANTA_A_PLANTA
        4. lo que ni derivando entra -> BYPASS
        5. recalcular retenidos sobre el volumen realmente tratado

    derivaciones : list[dict] | None
        Derivaciones que ENTRAN a esta planta desde otra, tal como las devuelve
        calcular_DERIVACION. Se inyectan dentro de io_plantas ANTES de calcular
        Volumen_relativo, así el gas derivado pesa en la mezcla de gas_rico_IN.

    MAX_DERIVACION_PLANTA_A_PLANTA : float
        Tope de lo que esta planta puede derivar hacia la SIGUIENTE, en la MISMA
        unidad que Volumen_inyectado. El volumen derivado sale de
        flujos['vol_derivado'] y se le pasa a calcular_DERIVACION.

    factor_retenidos : float
        Conversion de retenidos_vol a la unidad de CAPACIDAD_EVACUACION_TTY.
        Ver calcular_flujos_planta.

    capacidad_libre_destino : float | None
        Tope opcional adicional para la derivacion (cuanto puede absorber el
        destino).

    CAPACIDAD_TTY : float
        Capacidad de ingreso de gas. NO se usa para derivar ni bypasear (no es
        la restriccion activa); queda para reporte / KPI de ocupacion.
    """

    # Se arman los kwargs una sola vez para garantizar que todos los re-modelados
    # usen EXACTAMENTE las mismas derivaciones. Si se pasan solo en la primera
    # llamada, la derivacion entrante se pierde en silencio en los siguientes.
    comunes = dict(
        matriz_inyecciones = matriz_inyecciones,
        calcular_retenidos=calcular_retenidos,
        tabla_total_flujos_directos=tabla_total_flujos_directos,
        propiedades=propiedades,
        compuestos=COMPUESTOS,
        nombre_planta='TTY',
        derivaciones=derivaciones,
    )

    # 1) LGN potencial: lo que produciria tratando TODO el gas entrante.
    tabla_tty, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(**comunes, retenidos_planta=retenidos_TTY)

    retenidos_planta_final = retenidos_TTY


    # 2) Correccion de recuperacion: tratar lo mas posible antes de resignar gas.
    if retenidos_vol.values.sum() * factor_retenidos > CAPACIDAD_EVACUACION_TTY:

        correcciones, coef_corr_butanos, coef_corr_propano = correccion_TTY(tabla_tty=tabla_tty, retenidos_vol=retenidos_vol, propiedades=propiedades, gas_rico_IN=gas_rico_IN, retenidos=retenidos, CAPACIDAD_EVACUACION_TTY=CAPACIDAD_EVACUACION_TTY)

        new_retenidos = retenidos_TTY.T

        new_retenidos.loc[PROPANO] = coef_corr_propano.loc[PROPANO].fillna(0)



        for i in range(len(BUTANOS)):
            new_retenidos.loc[BUTANOS[i]] = np.ravel(coef_corr_butanos)[i]


        retenidos_planta_final = new_retenidos.T

        tabla_tty, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(**comunes, retenidos_planta=retenidos_planta_final)


    # 3) y 4) Excedente que sigue sin entrar: primero derivo, despues bypaseo.
    #    tabla_tty ya incluye las filas de derivacion ENTRANTE, entonces el
    #    reparto se hace sobre el gas que efectivamente llega.
    flujos = calcular_flujos_planta(
        tabla_planta=tabla_tty,
        retenidos_vol=retenidos_vol,
        CAPACIDAD_EVACUACION_PLANTA=CAPACIDAD_EVACUACION_TTY,
        MAX_DERIVACION_PLANTA_A_PLANTA=MAX_DERIVACION_PLANTA_A_PLANTA,
        factor_retenidos=factor_retenidos,
        capacidad_libre_destino=capacidad_libre_destino,
    )


    # 5) Retenidos sobre el gas realmente tratado. Por ser lineal en el volumen,
    #    esto deja el LGN justo en la capacidad de evacuacion: converge en un
    #    solo paso, no hace falta iterar.
    if flujos['excedente'] > 0:

        comunes['vol_procesado'] = flujos['vol_procesado']

        tabla_tty, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(**comunes, retenidos_planta=retenidos_planta_final)


    return {'tabla_total' : tabla_tty, 'gas_rico_IN' : gas_rico_IN, 'gas_residual_OUT' : gas_residual_OUT, 'retenidos' : retenidos, 'retenidos_vol' : retenidos_vol, 'flujos' : flujos, 'bypass' : flujos['bypass']}
