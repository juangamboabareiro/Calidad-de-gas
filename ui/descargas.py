"""
Descarga de la simulación completa.
===================================

Un solo botón y un solo archivo. "Descargar simulación" junta las dos cosas que
hoy están separadas y que el usuario no tiene por qué distinguir:

    escenario.json      la CONFIGURACION, para volver a cargarla después
    *.csv               los RESULTADOS, para mirarlos en Excel o mandarlos

Por qué un ZIP y no varios botones
----------------------------------
Un panel con seis botones de descarga obliga a entender qué es cada archivo
antes de bajarlo. Un ZIP con un LEEME adentro invierte eso: bajás una cosa y
después averiguás qué trajo. Para el usuario al que esto apunta es la diferencia
entre usarlo y no usarlo.

Y resuelve el problema de fondo: el botón está al lado de **Restablecer**, así
que su razón de ser es que nadie pierda veinte minutos de trabajo por un clic.
Si bajara sólo los resultados, la configuración se perdería igual.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pandas as pd


COLUMNAS_VOLUMEN = ["vol_disponible", "vol_maximo", "vol_asignado",
                    "sobrante", "vol_derivado", "bypass"]

LEEME = """SIMULACION DEL SANDBOX DE PLANTAS Y GASODUCTOS
=============================================

Generada el {fecha}

QUE HAY ACA
-----------
escenario.json
    La configuracion completa: las plantas y las intervenciones sobre ductos.
    Para volver a este mismo escenario, subilo en el sub-tab "Escenarios" del
    tab "Plantas (sandbox)" y apreta "Resolver cascada".

flujos_plantas.csv
    Una fila por planta con el reparto del gas. Volumenes en MMm3/d, LGN en
    tn/d. Vale, por planta:

        vol_disponible = vol_asignado + vol_derivado + bypass

    OJO: el vol_derivado de una planta es el vol_disponible de la siguiente,
    asi que NO se pueden sumar las columnas entre plantas.

impacto_por_planta.csv
    Cuanto gas y cuanto LGN gano o perdio cada planta respecto de la corrida
    oficial del tablero. Es la lectura que se busca al abrir o cerrar un ducto.

intervenciones_ductos.csv
    Que se abrio y que se saco de servicio, con el volumen involucrado.

detalle_<planta>.csv
    El pool de cada planta: de que area viene cada porcion de gas.
    `Volumen_pool` es el gas antes del reparto, `Volumen_inyectado` la porcion
    que esa planta trata efectivamente.

UNIDADES
--------
Los volumenes de los CSV estan en MMm3/d. El LGN en tn/d. En `escenario.json`
las capacidades estan en las unidades internas del modelo, no las edites a
mano: es mas seguro cargarlo y cambiarlas desde la pantalla.
"""


def _nombre_archivo(texto: str) -> str:
    """Nombre de archivo seguro a partir de un nombre de planta."""
    limpio = "".join(c if c.isalnum() or c in " -_" else "_" for c in str(texto))
    return limpio.strip().replace(" ", "_")[:60] or "planta"


def _csv(df: pd.DataFrame) -> str:
    return df.to_csv(index=False, encoding="utf-8")


def _flujos_en_mm(flujos, factor_mm) -> pd.DataFrame:
    vista = flujos.copy()
    for col in COLUMNAS_VOLUMEN:
        if col in vista.columns:
            vista[col] = pd.to_numeric(vista[col], errors="coerce") / factor_mm
    return vista.reset_index(names="Planta")


def _impacto(flujos_sandbox, flujos_produccion, factor_mm) -> pd.DataFrame | None:
    """Delta por planta contra la corrida oficial. None si no hay con qué comparar."""
    if flujos_produccion is None:
        return None

    filas = []

    for nombre in flujos_sandbox.index:
        despues = float(flujos_sandbox.loc[nombre, "vol_asignado"])
        lgn_despues = float(flujos_sandbox.loc[nombre, "lgn_asignado"])

        if nombre in flujos_produccion.index:
            antes = float(flujos_produccion.loc[nombre, "vol_asignado"])
            lgn_antes = float(flujos_produccion.loc[nombre, "lgn_asignado"])
            etiqueta = nombre
        else:
            antes = lgn_antes = 0.0
            etiqueta = f"{nombre} (nueva)"

        filas.append({
            "Planta": etiqueta,
            "Gas antes [MMm3/d]": antes / factor_mm,
            "Gas despues [MMm3/d]": despues / factor_mm,
            "Delta gas [MMm3/d]": (despues - antes) / factor_mm,
            "LGN antes [tn/d]": lgn_antes,
            "LGN despues [tn/d]": lgn_despues,
            "Delta LGN [tn/d]": lgn_despues - lgn_antes,
        })

    return pd.DataFrame(filas).sort_values("Delta gas [MMm3/d]", ascending=False)


def armar_zip(registro, intervenciones, plantas=None, flujos=None,
              flujos_produccion=None, informe=None, factor_mm=1000.0) -> bytes:
    """Arma el ZIP de la simulación.

    Funciona aunque no se haya resuelto la cascada todavía: en ese caso trae
    sólo `escenario.json`. Es a propósito — el caso "configuré todo y quiero
    guardarlo antes de restablecer" es tan válido como el de bajar resultados.
    """
    from ui.escenarios import serializar

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("LEEME.txt", LEEME.format(
            fecha=datetime.now().strftime("%d/%m/%Y %H:%M")))

        zf.writestr("escenario.json", serializar(registro, intervenciones))

        if intervenciones:
            zf.writestr("intervenciones_ductos.csv", _csv(pd.DataFrame([
                {"Tipo": i.tipo, "Gasoducto": i.nombre,
                 "Area origen": i.area_origen or "",
                 "Planta destino": i.planta_destino or "",
                 "Volumen [MMm3/d]": i.volumen / factor_mm,
                 "Activa": i.activa}
                for i in intervenciones
            ])))

        if informe is not None:
            tabla = informe.tabla()
            if not tabla.empty:
                zf.writestr("intervenciones_detalle.csv", _csv(tabla))
            if informe.avisos or informe.errores:
                zf.writestr("intervenciones_avisos.txt", "\n".join(
                    [f"AVISO: {a}" for a in informe.avisos]
                    + [f"ERROR: {e}" for e in informe.errores]))

        if flujos is not None and len(flujos):
            zf.writestr("flujos_plantas.csv", _csv(_flujos_en_mm(flujos, factor_mm)))

            impacto = _impacto(flujos, flujos_produccion, factor_mm)
            if impacto is not None:
                zf.writestr("impacto_por_planta.csv", _csv(impacto))

        for nombre, datos in (plantas or {}).items():
            tabla = datos.get("tabla_total")
            if tabla is not None and len(tabla):
                zf.writestr(f"detalle_{_nombre_archivo(nombre)}.csv", _csv(tabla))

    return buffer.getvalue()


def nombre_zip() -> str:
    return f"simulacion_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
