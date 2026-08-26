"""
El modelo de planta, uno solo.
==============================

`modelar_TTY` y `modelar_MEGA` son el MISMO calculo. Puestos uno al lado del
otro, MEGA resulta ser TTY con tres parametros clavados:

    modelar_MEGA(...)  ==  modelar_TTY(..., MAX_DERIVACION_PLANTA_A_PLANTA=0.0,
                                            activa=True,
                                            vol_disponible=None)

O sea que no son dos modelos: es un modelo y dos juegos de features. Este
archivo tiene ese modelo, con todo lo que estaba fijo adentro subido a
parametro. Nada de fisica nueva — el cuerpo es el de `modelar_TTY`, paso por
paso, y `test_registro_plantas.py` lo demuestra corriendo los originales en
paralelo y comparando salida contra salida.

QUE ESTABA HARDCODEADO Y AHORA ES INPUT
---------------------------------------
    nombre_planta='TTY' / 'MEGA'   ->  planta.nombre_pool
    MAX_DERIVACION (un destino)    ->  planta.conexiones (N destinos con %)
    activa (fijo en MEGA)          ->  planta.activa
    vol_disponible (fijo en MEGA)  ->  planta.toma_volumen_del_pool
    CAPACIDAD_* de config          ->  planta.capacidad_*
    retenidos_RTP por nombre       ->  planta.retenidos

LOGICA (identica a la de TTY.py)
--------------------------------
1. Se modela el POOL completo (`io_plantas`) para obtener la mezcla y el LGN de
   referencia.
2. lgn_unitario = LGN de referencia / volumen del pool.
3. vol_maximo = capacidad_evacuacion / lgn_unitario -> cuanto gas puede tomar
   antes de llenarse. La evacuacion de LGN es la restriccion activa; la
   capacidad de ingreso entra solo como min() adicional. Fuera de servicio,
   vol_maximo = 0.
4. Se llena hasta vol_maximo, se DERIVA el sobrante y el resto es BYPASS.
5. Los retenidos se escalan pro-rata al volumen asignado: son lineales en el
   volumen, asi que no hace falta re-modelar.

La unica diferencia con TTY.py esta en el paso 4, y no cambia ningun numero:
donde TTY tenia UN destino con un tope, aca puede haber N destinos con
proporciones. Se llama a `repartir_flujo_planta` con el tope en infinito y
despues se reparte el `sobrante` que devuelve. Es exacto, no una aproximacion:
`vol_asignado` sale de `min(vol_disponible, vol_maximo)` y no depende de como se
reparta lo que sobro; `retenidos` se escala sobre `vol_asignado`, tampoco.
"""

from pipeline.plantas.reparto_proporcional import repartir_entre_destinos


INFINITO = float("inf")


def _dependencias():
    """Importa `io_plantas` y los helpers de flujo EN CADA LLAMADA, a proposito.

    `app.py` hace `importlib.reload` de `planta_template`, `flujo_plantas` y
    `ctes_gas` cada vez que el usuario cambia un parametro de la sidebar
    (`_actualizar_config_y_recargar`). Con un `from ... import x` arriba del
    archivo, este modulo se quedaria con la version del PRIMER import y podria
    seguir usando constantes viejas despues de un reload.

    El costo es un lookup por llamada, contra el riesgo de mostrar numeros de
    una configuracion que el usuario ya cambio. No hay comparacion.
    """
    from pipeline.plantas.planta_template import io_plantas
    from pipeline.plantas.flujo_plantas import (
        calcular_lgn_unitario,
        calcular_volumen_maximo,
        repartir_flujo_planta,
    )
    return io_plantas, calcular_lgn_unitario, calcular_volumen_maximo, repartir_flujo_planta


def modelar_planta(planta, comunes, vol_disponible=None, derivaciones=None):
    """Modela una planta cualquiera como eslabon de la cascada.

    Parameters
    ----------
    planta : PlantaConfig
        Los features. Ver `registro.crear_planta`.
    comunes : dict
        El mismo que ya se arma en main.py / app.py: matriz_inyecciones,
        calcular_retenidos, tabla_total_flujos_directos,
        tabla_total_yacimientos, tabla_total_hubs, mapa_area_hub,
        propiedades, COMPUESTOS. Las claves de hubs son opcionales.
    vol_disponible : float | None
        Gas que llega a esta planta. None = todo su pool. Lo pasa la cascada
        para las plantas que reciben el volumen de otro tren sobre el mismo gas
        (el caso TTY-DP, que recibe el sobrante de TTY-TBX).
    derivaciones : list[dict] | None
        Gas que llega con OTRA composicion y tiene que pesar en la mezcla.
    """

    io_plantas, calcular_lgn_unitario, calcular_volumen_maximo, repartir_flujo_planta = (
        _dependencias())

    compuestos = comunes["COMPUESTOS"]

    # Las cromatografias cargadas por archivo aparte y las derivaciones reales
    # son lo mismo para el pool: volumen con composicion propia. Entran por el
    # mismo parametro, asi que `planta_template` no se toca.
    entradas = list(derivaciones or []) + list(planta.cromas_extra or [])

    tabla_pool, gas_rico_IN, gas_residual_OUT, retenidos_pool, retenidos_vol_pool = io_plantas(
        matriz_inyecciones=comunes.get("matriz_inyecciones"),
        calcular_retenidos=comunes["calcular_retenidos"],
        tabla_total_flujos_directos=comunes["tabla_total_flujos_directos"],
        tabla_total_yacimientos=comunes.get("tabla_total_yacimientos"),
        # `.get` y no acceso directo: los tests del tab arman `comunes` a mano
        # sin estas claves y no tienen por que conocerlas.
        tabla_total_hubs=comunes.get("tabla_total_hubs"),
        mapa_area_hub=comunes.get("mapa_area_hub"),
        propiedades=comunes["propiedades"],
        compuestos=compuestos,
        retenidos_planta=planta.retenidos,
        nombre_planta=planta.nombre_pool or planta.nombre,
        derivaciones=entradas or None,
    )

    vol_pool = float(tabla_pool["Volumen_inyectado"].values.sum())

    # `toma_volumen_del_pool=False` significa que el gas se lo pasa el eslabon
    # anterior: el pool sirve para la cromatografia, no para el volumen.
    if vol_disponible is None:
        vol_disponible = vol_pool if planta.toma_volumen_del_pool else 0.0

    lgn_unitario = calcular_lgn_unitario(vol_pool, retenidos_vol_pool)

    if planta.activa:
        vol_maximo = calcular_volumen_maximo(
            lgn_unitario=lgn_unitario,
            CAPACIDAD_EVACUACION_PLANTA=planta.capacidad_evacuacion,
            CAPACIDAD_INGRESO_PLANTA=planta.capacidad_ingreso,
        )
    else:
        # Tren fuera de servicio: no toma nada, todo pasa de largo.
        vol_maximo = 0.0

    deriva = bool(planta.deriva and planta.conexiones)

    # Tope en infinito y reparto despues: ver el docstring del modulo.
    flujos = repartir_flujo_planta(
        vol_disponible=vol_disponible,
        vol_maximo=vol_maximo,
        MAX_DERIVACION_PLANTA_A_PLANTA=(INFINITO if deriva else 0.0),
    )

    conexiones = [
        {"destino": c.destino, "proporcion": c.proporcion, "tope": c.tope}
        for c in planta.conexiones
    ] if deriva else []

    derivados, bypass = repartir_entre_destinos(
        monto=flujos["sobrante"],
        conexiones=conexiones,
        # Fuera de servicio el tope de traspaso no aplica: el tren no existe,
        # el gas pasa de largo. Es lo que en main.py se hacia pasandole
        # float('inf') como MAX_DERIVACION cuando TBX estaba pre-PM.
        ignorar_topes=not planta.activa,
    )

    flujos["derivados"] = derivados
    flujos["vol_derivado"] = sum(derivados.values())
    flujos["bypass"] = bypass
    flujos["lgn_unitario"] = lgn_unitario
    flujos["lgn_asignado"] = lgn_unitario * flujos["vol_asignado"]
    flujos["activa"] = planta.activa

    # Escalado pro-rata al volumen asignado. Volumen_relativo y la cromato no
    # cambian: es el mismo gas, solo una porcion.
    escala = (flujos["vol_asignado"] / vol_pool) if vol_pool else 0.0

    tabla = tabla_pool.copy()
    tabla["Volumen_pool"] = tabla_pool["Volumen_inyectado"]
    tabla["Volumen_inyectado"] = tabla_pool["Volumen_inyectado"] * escala

    return {
        "tabla_total": tabla,
        "gas_rico_IN": gas_rico_IN,
        "gas_residual_OUT": gas_residual_OUT,
        "retenidos": retenidos_pool * escala,
        "retenidos_vol": retenidos_vol_pool * escala,
        "flujos": flujos,
        "bypass": bypass,
        "vol_pool": vol_pool,
        "color": planta.color,
        "config": planta,
    }
