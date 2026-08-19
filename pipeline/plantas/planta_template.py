import pandas as pd
import numpy as np
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas




def _fila_derivacion(derivacion, compuestos):
    """Convierte una derivacion (salida de calcular_DERIVACION) en una fila de
    input de planta: Area + Volumen_inyectado + fracciones molares."""

    cromato = derivacion['cromato_derivacion']

    if isinstance(cromato, pd.DataFrame):
        cromato = cromato.squeeze()

    # reindex por NOMBRE, no por posicion: dict(zip(COMPUESTOS, cromato))
    # apareaba por orden y se rompia en silencio si el indice cambiaba de orden.
    cromato = cromato.reindex(compuestos).fillna(0)

    fila = cromato.to_dict()
    fila['Area'] = derivacion.get('origen', 'derivacion')
    fila['Volumen_inyectado'] = float(derivacion['vol_derivacion'])

    return fila


def io_plantas(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos, propiedades, compuestos, retenidos_planta, nombre_planta, derivaciones=None):
    """Modela el POOL completo que llega a una planta.

    Devuelve el escenario de referencia: la mezcla (gas_rico_IN) y los retenidos
    que saldrian si la planta tratara TODO el pool. Sobre ese resultado, quien
    llama calcula el LGN por unidad de volumen y decide cuanto gas asigna
    realmente (ver flujo_plantas.calcular_lgn_unitario / repartir_flujo_planta).

    No aplica capacidades ni bypass: eso se resuelve afuera, escalando pro-rata,
    porque los retenidos son lineales en el volumen y la cromato no cambia al
    tomar una porcion del mismo gas.

    derivaciones : list[dict] | None
        Gas que llega desde otra planta con OTRA composicion (caso TTY-DP ->
        MEGA). Se suma como fila de input ANTES de calcular Volumen_relativo,
        asi pesa en la mezcla. Para trenes que comparten pool (TBX -> DP) no se
        usa: ahi la cromato es la misma y solo cambia el volumen asignado.
    """

    tabla_plantas = pd.DataFrame()
    tabla_plantas['Area'] = matriz_inyecciones[nombre_planta]
    tabla_plantas['Area'] = tabla_plantas['Area'].apply(normalizar)


    tabla_plantas = tabla_plantas.merge(
        tabla_total_flujos_directos,
        on='Area',
        how='inner'
    )


    if derivaciones:
        filas = [_fila_derivacion(d, compuestos) for d in derivaciones
                 if float(d['vol_derivacion']) != 0]
        if filas:
            tabla_plantas = pd.concat([tabla_plantas, pd.DataFrame(filas)], ignore_index=True)


    tabla_plantas['Volumen_relativo'] = tabla_plantas['Volumen_inyectado']/(tabla_plantas['Volumen_inyectado'].values.sum())

    tabla_plantas = tabla_plantas.fillna(0)

    gas_rico_IN = tabla_plantas[compuestos].T.dot(tabla_plantas['Volumen_relativo'])


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
