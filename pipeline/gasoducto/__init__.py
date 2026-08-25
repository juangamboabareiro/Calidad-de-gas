"""
Intervenciones sobre la red de gasoductos.
==========================================

Altas y bajas de ductos que se aplican sobre las tablas de entrada de la cascada
ANTES de resolverla, para poder preguntarse "que pasa si abro un ducto de tal
area a tal planta" o "que pasa si saco tal ducto por mantenimiento".

El invariante que organiza todo: el volumen que inyecta cada AREA no cambia. Un
ducto no crea ni destruye gas, solo cambia por donde sale.

Uso:

    from pipeline.gasoductos import Intervencion, aplicar_intervenciones

    yac, fdi, matriz, informe = aplicar_intervenciones(
        tabla_yacimientos=..., tabla_flujos_directos=...,
        intervenciones=[Intervencion("baja", "VMS")],
        compuestos=COMPUESTOS, matriz_inyecciones=...)

NOTA: este archivo tiene contenido A PROPOSITO. Un `__init__.py` vacio es lo
primero que se pierde al copiar carpetas a mano o al armar un commit, y el error
que sale cuando falta es `ModuleNotFoundError: No module named
'pipeline.gasoductos'`, que no dice nada sobre la causa real.
"""

from pipeline.gasoductos.intervenciones import (
    Informe,
    Intervencion,
    aplicar_intervenciones,
    areas_disponibles,
    destinos_area,
    gasoductos_disponibles,
    volumen_area,
)

__all__ = [
    "Informe",
    "Intervencion",
    "aplicar_intervenciones",
    "areas_disponibles",
    "destinos_area",
    "gasoductos_disponibles",
    "volumen_area",
]
