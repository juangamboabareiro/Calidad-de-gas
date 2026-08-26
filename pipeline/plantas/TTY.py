import pandas as pd
import numpy as np
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas
from pipeline.plantas.flujo_plantas import (
    calcular_lgn_unitario,
    calcular_volumen_maximo,
    repartir_flujo_planta,
    calcular_DERIVACION,
)





def correccion_TTY(retenidos_vol, tabla_tty, propiedades, gas_rico_IN, retenidos, CAPACIDAD_EVACUACION_TTY):
    """Baja la recuperacion de C3/C4 para meter el LGN dentro de la evacuacion.

    NO esta en el camino del modelo de cascada: con la logica de
    llenar -> derivar -> bypasear, a la planta se le asigna exactamente el
    volumen cuyo LGN entra en la evacuacion, entonces esta correccion nunca
    tendria que dispararse (y si se disparara, absorberia el excedente y la
    derivacion quedaria en cero, que es el sintoma que veniamos viendo).

    Se conserva porque codifica algo que la cascada no modela: que la gasolina
    pasa 100%, que no se trata etano, y que el limite se llena primero con C4 y
    despues con C3. Si el limite de evacuacion aplica solo a C3+C4 y no a los
    cuatro cortes, hay que revisar calcular_lgn_unitario, no esta funcion.
    """

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




def modelar_TTY(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos,
                propiedades, COMPUESTOS, retenidos_TTY, CAPACIDAD_EVACUACION_TTY,
                vol_disponible=None, MAX_DERIVACION_PLANTA_A_PLANTA=0.0,
                CAPACIDAD_TTY=None, derivaciones=None, activa=True,
                tabla_total_yacimientos=None,
                tabla_total_hubs=None, mapa_area_hub=None):
    """Modela un tren TTY (Dew Point o TBX) como eslabon de la cascada.

    LOGICA
    ------
    1. Se modela el POOL completo (io_plantas) para obtener la mezcla y el LGN
       de referencia.
    2. lgn_unitario = LGN de referencia / volumen del pool.
    3. vol_maximo = CAPACIDAD_EVACUACION_TTY / lgn_unitario  -> cuanto gas puede
       tomar antes de llenarse. La evacuacion de LGN es la restriccion activa;
       CAPACIDAD_TTY (ingreso de gas) entra solo como min() adicional.
    4. Se llena hasta vol_maximo, se DERIVA el sobrante hasta
       MAX_DERIVACION_PLANTA_A_PLANTA, y el resto es BYPASS.
    5. Los retenidos se escalan pro-rata al volumen asignado: son lineales en el
       volumen, asi que no hace falta re-modelar.

    vol_disponible : float | None
        Gas que llega a este tren. None = todo el pool de matriz_inyecciones.
        Post-PM, TTY-DP recibe solo el sobrante de TTY-TBX, entonces se le pasa
        ese volumen y la tabla del pool se escala pro-rata (misma cromato: los
        dos trenes trabajan sobre el mismo gas).

    activa : bool
        False para un tren fuera de servicio (TTY-TBX antes de la fecha de PM):
        no toma nada y todo su gas disponible pasa como derivacion al siguiente.
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
        retenidos_planta=retenidos_TTY,
        nombre_planta='TTY',
        derivaciones=derivaciones,
    )

    vol_pool = float(tabla_pool['Volumen_inyectado'].values.sum())

    if vol_disponible is None:
        vol_disponible = vol_pool

    lgn_unitario = calcular_lgn_unitario(vol_pool, retenidos_vol_pool)

    if activa:
        vol_maximo = calcular_volumen_maximo(
            lgn_unitario=lgn_unitario,
            CAPACIDAD_EVACUACION_PLANTA=CAPACIDAD_EVACUACION_TTY,
            CAPACIDAD_INGRESO_PLANTA=CAPACIDAD_TTY,
        )
    else:
        # Tren fuera de servicio: no toma nada, todo pasa de largo.
        vol_maximo = 0.0

    flujos = repartir_flujo_planta(
        vol_disponible=vol_disponible,
        vol_maximo=vol_maximo,
        MAX_DERIVACION_PLANTA_A_PLANTA=MAX_DERIVACION_PLANTA_A_PLANTA,
    )

    flujos['lgn_unitario'] = lgn_unitario
    flujos['lgn_asignado'] = lgn_unitario * flujos['vol_asignado']
    flujos['activa'] = activa

    # Escalado pro-rata al volumen asignado. Volumen_relativo y la cromato no
    # cambian: es el mismo gas, solo una porcion.
    escala = (flujos['vol_asignado'] / vol_pool) if vol_pool else 0.0

    tabla_tty = tabla_pool.copy()
    tabla_tty['Volumen_pool'] = tabla_pool['Volumen_inyectado']
    tabla_tty['Volumen_inyectado'] = tabla_pool['Volumen_inyectado'] * escala

    retenidos = retenidos_pool * escala
    retenidos_vol = retenidos_vol_pool * escala

    return {
        'tabla_total': tabla_tty,
        'gas_rico_IN': gas_rico_IN,
        'gas_residual_OUT': gas_residual_OUT,
        'retenidos': retenidos,
        'retenidos_vol': retenidos_vol,
        'flujos': flujos,
        'bypass': flujos['bypass'],
    }
