"""
Captura de los mensajes de diagnostico del pipeline para mostrarlos en la app.

El problema
-----------
`merge_validado`, `_descartar_filas_sin_area` y compania avisan por `print`.
Eso sirve corriendo `main.py` desde la terminal, pero en Streamlit el proceso
del servidor se lleva esos mensajes y quien mira el navegador no ve nada. Un
merge que perdio 200 filas queda invisible justo para la persona que esta
mirando los numeros.

La solucion
-----------
Se redirige stdout mientras corre el pipeline, se clasifican las lineas y se
muestran en pantalla. Los avisos importantes salen como warning; el resto como
informacion plegable.

Uso
---
    from ui.diagnosticos import capturar, mostrar

    with capturar() as registro:
        resultados = ejecutar_pipeline(...)

    mostrar(registro)
"""

from __future__ import annotations

import contextlib
import io

import streamlit as st


# Marcas que elevan un mensaje a advertencia visible.
#
# Criterio: solo van las marcas que aparecen UNICAMENTE cuando hay algo para
# mirar. Los chequeos nuevos imprimen tambien lineas de resumen que salen
# siempre (por ejemplo "[tabla_total_yacimientos] 0 por ruta, 122 por clave,
# 20 sin resolver", o "[input_planta:MEGA] 11 origenes"). Si se marcaran esas,
# el panel estaria permanentemente en amarillo y dejaria de significar algo.
_MARCAS_ALERTA = (
    # merges y normalizacion
    "OJO",
    "sin match",
    "descartadas",
    "duplicad",
    # validacion de destinos de la matriz (preprocesamiento)
    "FALTAN",
    "coinciden solo por formato",
    "no son destino",
    # cromatografia
    "SIN cromatografia",
    "DISTINTAS",
    "suma molar",
    "no tiene columna",
    # inyeccion / yacimientos
    "sin volumen",
    "sin destino",
    "revisar pares",
    # armado del pool de plantas
    "sin tabla_total_yacimientos",
    "matriz vs destino",
    "sin filas",
)

# Marcas de error duro: algo quedo mal configurado y el resultado no es
# confiable, aunque el pipeline no haya reventado.
_MARCAS_ERROR = (
    "SIN cromatografia",
    "sin tabla_total_yacimientos",
    "FALTAN",
)


@contextlib.contextmanager
def capturar():
    """
    Context manager que junta todo lo que el pipeline imprime.

    Yields
    ------
    list[str]
        Lista que se llena al salir del bloque. Ojo: adentro del `with`
        todavia esta vacia.
    """
    registro: list[str] = []
    buffer = io.StringIO()

    try:
        with contextlib.redirect_stdout(buffer):
            yield registro
    finally:
        registro.extend(
            linea for linea in buffer.getvalue().splitlines() if linea.strip()
        )


def clasificar(registro: list[str]) -> tuple[list[str], list[str], list[str]]:
    """
    Separa los mensajes en errores, alertas y notas.

    Returns
    -------
    errores, alertas, notas : list[str], list[str], list[str]
        `errores` es un subconjunto de lo que antes eran alertas: casos donde
        el resultado numerico queda comprometido (gas sin cromatografia, un
        pool de planta armado sin yacimientos, destinos ausentes de la matriz).
    """
    errores = [l for l in registro if any(m in l for m in _MARCAS_ERROR)]
    alertas = [
        l for l in registro
        if l not in errores and any(m in l for m in _MARCAS_ALERTA)
    ]
    notas = [l for l in registro if l not in errores and l not in alertas]

    return errores, alertas, notas


def resumen_cromatografia(registro: list[str]) -> dict:
    """
    Extrae el conteo de filas resueltas por cada tabla total.

    Las lineas tienen la forma:
        [tabla_total_yacimientos] 0 por ruta, 122 por clave, 20 sin resolver

    Returns
    -------
    dict[str, dict]
        nombre de tabla -> {'ruta': int, 'clave': int, 'sin_resolver': int}
    """
    import re

    patron = re.compile(
        r"\[(\w+)\]\s+(\d+)\s+por ruta,\s+(\d+)\s+por clave,\s+(\d+)\s+sin resolver"
    )

    salida = {}
    for linea in registro:
        m = patron.search(linea)
        if m:
            salida[m.group(1)] = {
                "ruta": int(m.group(2)),
                "clave": int(m.group(3)),
                "sin_resolver": int(m.group(4)),
            }

    return salida


def mostrar(registro: list[str], titulo: str = "Diagnostico del pipeline") -> None:
    """
    Renderiza los mensajes capturados.

    Errores arriba y visibles, alertas debajo, y el detalle completo en un
    expander para no tapar los resultados.
    """
    if not registro:
        st.success("Pipeline sin observaciones: ningun merge perdio filas.")
        return

    errores, alertas, notas = clasificar(registro)

    if errores:
        st.error(
            f"**{len(errores)} observaciones que afectan los numeros.** "
            "El pipeline corrio, pero hay gas entrando sin cromatografia o "
            "flujos que no llegan a ninguna tabla."
        )
        for linea in errores:
            st.markdown(f"- `{linea}`")

    if alertas:
        st.warning(
            f"**{len(alertas)} observaciones sobre los datos de entrada.** "
            "No frenan el calculo, pero conviene revisarlas."
        )
        for linea in alertas:
            st.markdown(f"- `{linea}`")

    if not errores and not alertas:
        st.success("Pipeline sin observaciones.")

    resumen = resumen_cromatografia(registro)
    if resumen:
        with st.expander("Resolucion de cromatografia por tabla"):
            import pandas as pd

            st.dataframe(
                pd.DataFrame(resumen).T.rename(
                    columns={
                        "ruta": "Por ruta (Area, Gasoducto)",
                        "clave": "Por clave (Area+Sufijo)",
                        "sin_resolver": "Sin resolver",
                    }
                ),
                use_container_width=True,
            )
            st.caption(
                "'Por ruta' cuenta las filas resueltas con premisa de gasoducto; "
                "'por clave', con premisa de area. 'Sin resolver' tiene que dar 0: "
                "esas filas entran al modelo como gas vacio."
            )

    with st.expander(f"{titulo} — {len(registro)} mensajes"):
        if notas:
            st.code("\n".join(notas), language="text")
        else:
            st.caption("Sin mensajes informativos ademas de las alertas.")
