"""
Resolucion de la cascada: orden, traspaso de gas y grafo.
=========================================================

Este modulo no calcula nada de la planta: eso es `planta.modelar_planta`, uno
solo para todas. Aca solo se decide EN QUE ORDEN se resuelven y COMO se pasa el
gas de una a otra.
"""

import pandas as pd

from pipeline.plantas.registro import orden_topologico, PlantaConfig
from pipeline.plantas.planta import modelar_planta


COLUMNAS_FLUJOS = [
    "vol_disponible", "vol_maximo", "vol_asignado", "sobrante",
    "vol_derivado", "bypass", "lgn_unitario", "lgn_asignado", "activa",
]


def resolver_cascada(registro: dict[str, PlantaConfig], comunes: dict):
    """Recorre el registro en orden topologico. Devuelve (plantas, flujos_df).

    Cada planta se resuelve DESPUES de todas las que le mandan gas, asi que
    cuando le toca el turno ya tiene su volumen entrante completo.

    Dos formas de recibir gas, que se acumulan distinto:

      - `comparte_pool=True`  -> suma a `volumen`. Mismo gas, misma cromato:
        solo cambia el volumen. Es el caso TBX -> DP.
      - `comparte_pool=False` -> va a `derivaciones`. Otro pool, otra
        composicion: tiene que entrar a la mezcla. Es el caso DP -> MEGA.

    Una planta que recibe por las dos vias es legal: el volumen del mismo pool
    manda el `vol_disponible` y las derivaciones ademas mueven la cromatografia.
    """

    orden = orden_topologico(registro)

    entrantes = {n: {"volumen": 0.0, "derivaciones": [], "recibe_de_vol": 0.0}
                 for n in registro}
    plantas: dict[str, dict] = {}

    for nombre in orden:
        planta = registro[nombre]
        entrada = entrantes[nombre]

        # Cabecera: `vol_disponible=None` deja que el modelo tome todo su pool.
        # Eslabon intermedio: el volumen es el que le derivaron.
        vol = None if planta.toma_volumen_del_pool else entrada["volumen"]

        resultado = modelar_planta(
            planta=planta,
            comunes=comunes,
            vol_disponible=vol,
            derivaciones=entrada["derivaciones"] or None,
        )

        # Cabecera que ADEMAS recibe volumen del mismo pool: hay que sumarlo y
        # volver a repartir, si no el gas desaparece. Ninguna de las tres de
        # siempre lo usa, pero un escenario nuevo puede armarlo.
        if planta.toma_volumen_del_pool and entrada["volumen"] > 0:
            resultado = modelar_planta(
                planta=planta,
                comunes=comunes,
                vol_disponible=resultado["vol_pool"] + entrada["volumen"],
                derivaciones=entrada["derivaciones"] or None,
            )

        resultado["recibe_de_vol"] = entrada["recibe_de_vol"]
        plantas[nombre] = resultado

        for conexion in (planta.conexiones if planta.deriva else []):
            destino = conexion.destino
            if destino not in entrantes:
                continue
            volumen = resultado["flujos"]["derivados"].get(destino, 0.0)
            if volumen <= 0:
                continue

            entrantes[destino]["recibe_de_vol"] += volumen

            if conexion.comparte_pool:
                entrantes[destino]["volumen"] += volumen
            else:
                entrantes[destino]["derivaciones"].append({
                    "vol_derivacion": volumen,
                    "cromato_derivacion": resultado["gas_rico_IN"],
                    "origen": nombre,
                })

    flujos_df = pd.DataFrame(
        {n: plantas[n]["flujos"] for n in orden}
    ).T.reindex(columns=COLUMNAS_FLUJOS)

    return plantas, flujos_df


def desvio_balance(flujos_df) -> float:
    """Maximo |vol_disponible - vol_asignado - vol_derivado - bypass|.

    El `vol_derivado` de un eslabon es el `vol_disponible` del siguiente, asi
    que la cadena cierra sin doble conteo.
    """
    return float(
        (flujos_df["vol_disponible"]
         - flujos_df[["vol_asignado", "vol_derivado", "bypass"]].sum(axis=1))
        .abs().max()
    )


def dot_cascada(registro, plantas, factor_mm=1000.0) -> str:
    """Graphviz armado desde el registro.

    Reemplaza a `_dot_cascada` de app.py, que tenia los tres nombres escritos a
    mano y por eso no podia dibujar una planta nueva. Las plantas que comparten
    `nombre_pool` cuelgan del MISMO nodo, que es lo que hace visible de un
    vistazo que dos trenes trabajan sobre el mismo gas.
    """

    def fmt(v):
        return "—" if v is None else f"{v / factor_mm:,.2f}"

    lineas = [
        "digraph G {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fontname="Arial", fontsize=10];',
        '  edge [fontname="Arial", fontsize=9];',
        '  byp [label="ByPass", shape=ellipse, fillcolor="#FADBD8"];',
    ]

    pools = {}
    for nombre, planta in registro.items():
        estilo = "" if planta.activa else ', style="rounded,filled,dashed"'
        lineas.append(f'  "{nombre}" [fillcolor="{planta.color}"{estilo}];')

        if planta.toma_volumen_del_pool:
            pool = planta.nombre_pool or nombre
            if pool not in pools:
                pools[pool] = f"pool_{len(pools)}"
                lineas.append(
                    f'  {pools[pool]} [label="Pool {pool}", fillcolor="#FDEBD0"];')
            vol = plantas.get(nombre, {}).get("flujos", {}).get("vol_disponible")
            lineas.append(f'  {pools[pool]} -> "{nombre}" [label="{fmt(vol)}"];')

    for nombre, planta in registro.items():
        flujos = plantas.get(nombre, {}).get("flujos", {})
        for conexion in (planta.conexiones if planta.deriva else []):
            vol = flujos.get("derivados", {}).get(conexion.destino, 0.0)
            estilo = "" if conexion.comparte_pool else ", style=bold"
            lineas.append(
                f'  "{nombre}" -> "{conexion.destino}" [label="{fmt(vol)}"{estilo}];')
        if flujos.get("bypass", 0) > 0:
            lineas.append(
                f'  "{nombre}" -> byp [label="{fmt(flujos["bypass"])}", style=dashed];')

    lineas.append("}")
    return "\n".join(lineas)
