# import pandas as pd
import pandas as pd
import numpy as np
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS, CONVERSION_BARRILLES_KGD
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


from pipeline.plantas.flujo_plantas import calcular_flujos_planta, calcular_DERIVACION



# antes: factor_retenidos=1/1000
def modelar_MEGA(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_MEGA, CAPACIDAD_MEGA, CAPACIDAD_EVACUACION_MEGA, derivaciones=None, factor_retenidos=1.0, capacidad_libre_destino=None):
    """Modela MEGA.

    MEGA no tiene poder de derivacion: es la ultima planta de la cadena, entonces
    todo el excedente sobre la capacidad de evacuacion de LGN es BYPASS. Por eso
    no recibe MAX_DERIVACION_PLANTA_A_PLANTA (queda fijo en 0).

    El coef_correccion que estaba antes aca
        coef_correccion = CAPACIDAD_EVACUACION_MEGA/(retenidos_vol.sum()/1000)
        retenidos_vol   = retenidos_vol * coef_correccion
    es exactamente flujos['fraccion_tratable']: escalar los retenidos es lo mismo
    que decir que se trata solo vol_procesado. Ahora se hace via vol_procesado,
    asi el gas no tratado queda explicito como bypass en vez de desaparecer.

    factor_retenidos : float
        Conversion de retenidos_vol a la unidad de CAPACIDAD_EVACUACION_MEGA.
        Default 1/1000 para reproducir el /1000 (kg/d -> tn/d) que tenia el if.

    derivaciones : list[dict] | None
        Derivaciones que ENTRAN a MEGA (tipicamente desde TTY-DP). Se inyectan
        dentro de io_plantas antes de calcular Volumen_relativo, asi el gas
        derivado entra en la mezcla de gas_rico_IN en vez de quedar colgado en
        una fila que nadie lee.
    """

    comunes = dict(
        matriz_inyecciones = matriz_inyecciones,
        calcular_retenidos=calcular_retenidos,
        tabla_total_flujos_directos=tabla_total_flujos_directos,
        propiedades=propiedades,
        compuestos=COMPUESTOS,
        nombre_planta='MEGA',
        derivaciones=derivaciones,
    )

    # LGN potencial: lo que produciria tratando TODO el gas entrante.
    tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(**comunes, retenidos_planta=retenidos_MEGA)

    flujos = calcular_flujos_planta(
        tabla_planta=tabla_mega,
        retenidos_vol=retenidos_vol,
        CAPACIDAD_EVACUACION_PLANTA=CAPACIDAD_EVACUACION_MEGA,
        MAX_DERIVACION_PLANTA_A_PLANTA=0.0,
        factor_retenidos=factor_retenidos,
        capacidad_libre_destino=capacidad_libre_destino,
    )

    # Retenidos sobre el gas realmente tratado (equivale al viejo coef_correccion).
    if flujos['excedente'] > 0:

        comunes['vol_procesado'] = flujos['vol_procesado']

        tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(**comunes, retenidos_planta=retenidos_MEGA)

    return {'tabla_total' : tabla_mega, 'gas_rico_IN' : gas_rico_IN, 'gas_residual_OUT' : gas_residual_OUT, 'retenidos' : retenidos, 'retenidos_vol' : retenidos_vol, 'flujos' : flujos, 'bypass' : flujos['bypass']}
