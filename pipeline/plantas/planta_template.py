import pandas as pd
import numpy as np
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO
import config
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas


def _seleccionar_destino(tabla, nombre_planta, compuestos, origen_tabla):
    """Filas de una tabla_total cuyo destino es `nombre_planta`.

    Replica SUMIFS(..., HUBs!$J:$J, planta) del Excel: la columna Gasoducto ES
    el destino, entonces filtrar por ella deja solo el gas que efectivamente
    entra a la planta. Se compara normalizado porque el nombre del gasoducto
    llega escrito distinto segun de que hoja venga.
    """
    columnas = ['Area', 'HUB', 'Gasoducto', 'Volumen_inyectado'] + list(compuestos)

    if tabla is None or not len(tabla):
        return pd.DataFrame(columns=columnas + ['Origen_tabla'])

    seleccion = tabla[tabla['Gasoducto'].map(normalizar) == normalizar(nombre_planta)].copy()

    # reindex y no [columnas]: si a una de las dos tablas le falta HUB o algun
    # compuesto, el concat alinearia por nombre y metaria NaN sin avisar.
    seleccion = seleccion.reindex(columns=columnas)
    seleccion['Origen_tabla'] = origen_tabla

    return seleccion

def armar_input_planta(tabla_total_flujos_directos, tabla_total_yacimientos,
                       nombre_planta, compuestos, matriz_inyecciones=None,
                       tabla_total_hubs=None, mapa_area_hub=None):
    """Gas que ingresa a una planta: todas las filas cuyo destino es la planta.

    Union de TRES fuentes:
    - flujos_directos: origenes que son gasoductos.
    - yacimientos: areas SIN hub que inyectan directo. Las areas con hub ya
      no aparecen aca con destino planta: `calcular_ruteo_hubs` les saco esas
      filas y las convirtio en filas de hub.
    - hubs: gas que llega desde un HUB, con la croma del hub (premisa de
      Cromas-HUBs o mezcla volumetrica de sus areas) y el reparto de
      Detalles-HUBs.

    No hay doble conteo: el aporte de un area via un gasoducto ya viene
    agregado dentro de la fila de ese gasoducto, y el aporte via hub viene
    en la fila del hub (la fila directa area->planta se elimino al rutear).

    mapa_area_hub : dict | None
        {area normalizada -> clave de hub} de las areas ruteadas. Se usa para
        traducir la validacion contra la matriz de inyecciones: la matriz
        declara AREAS como origen, pero en el pool esas areas aparecen ahora
        como su hub.
    """
    partes = [
        _seleccionar_destino(tabla_total_flujos_directos, nombre_planta, compuestos, 'flujos_directos'),
        _seleccionar_destino(tabla_total_yacimientos, nombre_planta, compuestos, 'yacimientos'),
        _seleccionar_destino(tabla_total_hubs, nombre_planta, compuestos, 'hubs'),
    ]

    entrada = pd.concat(partes, ignore_index=True)

    if not len(entrada):
        print(f"[input_planta:{nombre_planta}] sin filas: revisar el nombre del destino")
        return entrada

    dup = entrada.duplicated(['Area', 'Gasoducto'], keep=False)
    if dup.any():
        print(f"[input_planta:{nombre_planta}] OJO {int(dup.sum())} filas (Area,Gasoducto) repetidas entre tablas")

    if matriz_inyecciones is not None and nombre_planta in matriz_inyecciones.columns:
        esperados = set(matriz_inyecciones[nombre_planta].dropna().map(normalizar))

        # La matriz declara areas; si un area fue ruteada, en el pool aparece
        # como su hub. Se traduce para que la validacion siga siendo util y no
        # reporte falsas diferencias tras el cambio de ruteo.
        if mapa_area_hub:
            esperados = {mapa_area_hub.get(a, a) for a in esperados}

        obtenidos = set(entrada['Area'].map(normalizar))
        faltan, sobran = sorted(esperados - obtenidos), sorted(obtenidos - esperados)
        if faltan or sobran:
            print(f"[input_planta:{nombre_planta}] matriz vs destino - sin volumen: {faltan} | fuera de matriz: {sobran}")

    print(f"[input_planta:{nombre_planta}] {len(entrada)} origenes, "
          f"{entrada['Volumen_inyectado'].sum():,.0f} de volumen")

    return entrada.reset_index(drop=True)




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

def io_plantas(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos,
               propiedades, compuestos, retenidos_planta, nombre_planta,
               derivaciones=None, tabla_total_yacimientos=None,
               tabla_total_hubs=None, mapa_area_hub=None):
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

    if tabla_total_yacimientos is None:
        print(f"[io_plantas:{nombre_planta}] sin tabla_total_yacimientos: "
              "las areas que inyectan directo NO entran al pool")

    tabla_plantas = armar_input_planta(
        tabla_total_flujos_directos=tabla_total_flujos_directos,
        tabla_total_yacimientos=tabla_total_yacimientos,
        nombre_planta=nombre_planta,
        compuestos=compuestos,
        matriz_inyecciones=matriz_inyecciones,
        tabla_total_hubs=tabla_total_hubs,
        mapa_area_hub=mapa_area_hub,
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
