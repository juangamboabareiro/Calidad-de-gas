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
muestran en pantalla. Los avisos con "OJO" salen como warning; el resto como
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
_MARCAS_ALERTA = ("OJO", "sin match", "descartadas", "duplicad")


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


def clasificar(registro: list[str]) -> tuple[list[str], list[str]]:
    """
    Separa los mensajes en alertas y notas.

    Returns
    -------
    alertas, notas : list[str], list[str]
    """
    alertas = [l for l in registro if any(m in l for m in _MARCAS_ALERTA)]
    notas = [l for l in registro if l not in alertas]

    return alertas, notas


def mostrar(registro: list[str], titulo: str = "Diagnostico del pipeline") -> None:
    """
    Renderiza los mensajes capturados.

    Las alertas van visibles; el detalle completo queda en un expander para no
    tapar los resultados.
    """
    if not registro:
        st.success("Pipeline sin observaciones: ningun merge perdio filas.")
        return

    alertas, notas = clasificar(registro)

    if alertas:
        st.warning(
            f"**{len(alertas)} observaciones sobre los datos de entrada.** "
            "No frenan el calculo, pero conviene revisarlas."
        )
        for linea in alertas:
            st.markdown(f"- `{linea}`")

    with st.expander(f"{titulo} — {len(registro)} mensajes"):
        if notas:
            st.code("\n".join(notas), language="text")
        else:
            st.caption("Sin mensajes informativos ademas de las alertas.")
