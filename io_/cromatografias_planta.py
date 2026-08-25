"""
Cromatografias de planta cargadas por archivo APARTE.
====================================================

Por que un archivo separado y no una hoja mas de `inputs.xlsx`:

  - `inputs.xlsx` es el tablero general, lo mantiene otra gente y tiene su
    propio ciclo de actualizacion. Meterle una hoja para probar una planta que
    todavia no existe obliga a versionar el tablero entero por un escenario.
  - Una planta nueva no aparece como destino en la matriz de inyecciones, asi
    que `armar_input_planta` (que filtra por `Gasoducto == nombre_planta`) le
    devuelve cero filas. El gas hay que inyectarlo a mano.

Como entra al modelo
--------------------
Cada fila se convierte en un dict con la misma forma que devuelve
`calcular_DERIVACION`:

    {'vol_derivacion': float, 'cromato_derivacion': Series, 'origen': str}

y se pasa por `derivaciones` a `io_plantas`, que la suma como fila del pool
ANTES de calcular `Volumen_relativo`. O sea: pesa en la mezcla de `gas_rico_IN`
exactamente igual que el gas que llega por gasoducto. No hace falta tocar
`planta_template.py`.

FORMATO ESPERADO
----------------
Un .xlsx (o .csv) con una fila por corriente:

    Planta        | Origen        | Volumen | metano | etano | propano | ...
    Planta Nueva  | Pozo X        |   12.5  | 0.85   | 0.07  | 0.03    | ...
    Planta Nueva  | Gasoducto Y   |    3.0  | 0.88   | 0.06  | 0.02    | ...

  - `Planta`  : a que planta del registro alimenta. Obligatoria.
  - `Origen`  : etiqueta libre, aparece en la tabla de detalle. Opcional.
  - `Volumen` : en MMm3/d por default (ver `unidad`).
  - resto     : una columna por compuesto. Los nombres se aparean contra
                COMPUESTOS ignorando tildes, espacios y mayusculas.

Fracciones molares. Si vienen en porcentaje (suma ~100) se detecta y se divide
por 100; si la suma no da ni 1 ni 100 se avisa y se normaliza, porque un pool
que no suma 1 rompe silenciosamente todos los calculos aguas abajo.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd


COL_PLANTA = "Planta"
COL_ORIGEN = "Origen"
COL_VOLUMEN = "Volumen"

_TOLERANCIA_SUMA = 0.02


def _clave(texto) -> str:
    """Normaliza un nombre de columna para poder aparearlo: sin tildes, sin
    separadores, minusculas. Mismo criterio que usa `ui/tablas.py`."""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def leer_archivo(origen) -> pd.DataFrame:
    """Acepta un path, un buffer de Streamlit o un DataFrame ya armado."""
    if isinstance(origen, pd.DataFrame):
        return origen.copy()

    nombre = getattr(origen, "name", str(origen))
    if str(nombre).lower().endswith((".csv", ".txt")):
        return pd.read_csv(origen)
    return pd.read_excel(origen)


def mapear_compuestos(columnas, compuestos) -> dict[str, str]:
    """{columna del archivo -> compuesto canonico}. Las columnas que no matchean
    con ningun compuesto se ignoran (pueden ser notas, unidades, lo que sea)."""
    canon = {_clave(c): c for c in compuestos}
    mapa = {}
    for col in columnas:
        destino = canon.get(_clave(col))
        if destino is not None:
            mapa[col] = destino
    return mapa


def cargar_cromas_extra(origen, compuestos, factor_volumen=1000.0,
                        unidad="MMm3/d") -> tuple[dict[str, list[dict]], list[str]]:
    """
    Returns
    -------
    (cromas, avisos)
        cromas : {nombre_planta: [ {vol_derivacion, cromato_derivacion, origen}, ... ]}
                 listo para asignar a `PlantaConfig.cromas_extra`.
        avisos : mensajes para mostrar en la UI (no rompen la carga).
    """

    avisos: list[str] = []
    df = leer_archivo(origen)

    if df.empty:
        return {}, ["El archivo de cromatografias esta vacio."]

    # Aparear las columnas de estructura tolerando tildes y mayusculas.
    estructura = {}
    for esperada in (COL_PLANTA, COL_ORIGEN, COL_VOLUMEN):
        for col in df.columns:
            if _clave(col) == _clave(esperada):
                estructura[esperada] = col
                break

    if COL_PLANTA not in estructura:
        return {}, [f"Falta la columna '{COL_PLANTA}': no se sabe a que planta va cada fila."]
    if COL_VOLUMEN not in estructura:
        return {}, [f"Falta la columna '{COL_VOLUMEN}'."]

    mapa = mapear_compuestos(df.columns, compuestos)
    if not mapa:
        return {}, ["Ninguna columna del archivo coincide con un compuesto conocido."]

    faltantes = sorted(set(compuestos) - set(mapa.values()))
    if faltantes:
        avisos.append(
            f"El archivo no trae {len(faltantes)} compuesto(s) "
            f"({', '.join(map(str, faltantes[:6]))}{'...' if len(faltantes) > 6 else ''}). "
            "Se completan en 0.")

    cromas: dict[str, list[dict]] = {}

    for i, fila in df.iterrows():
        planta = str(fila[estructura[COL_PLANTA]]).strip()
        if not planta or planta.lower() == "nan":
            avisos.append(f"Fila {i + 2}: sin planta, se descarta.")
            continue

        try:
            volumen = float(fila[estructura[COL_VOLUMEN]])
        except (TypeError, ValueError):
            avisos.append(f"Fila {i + 2} ({planta}): volumen no numerico, se descarta.")
            continue

        if volumen <= 0:
            avisos.append(f"Fila {i + 2} ({planta}): volumen {volumen}, se descarta.")
            continue

        cromato = pd.Series(0.0, index=list(compuestos), dtype="float64")
        for col_archivo, compuesto in mapa.items():
            valor = pd.to_numeric(fila[col_archivo], errors="coerce")
            cromato[compuesto] = 0.0 if pd.isna(valor) else float(valor)

        suma = float(cromato.sum())
        etiqueta = str(fila[estructura[COL_ORIGEN]]) if COL_ORIGEN in estructura else f"fila {i + 2}"

        if suma <= 0:
            avisos.append(f"Fila {i + 2} ({planta}): cromatografia toda en cero, se descarta.")
            continue

        if abs(suma - 100.0) < 1.0:
            # Vino en porcentaje. Es el error mas comun al armar el archivo a
            # mano desde el Excel de premisas.
            cromato = cromato / 100.0
            suma = float(cromato.sum())

        if abs(suma - 1.0) > _TOLERANCIA_SUMA:
            avisos.append(
                f"Fila {i + 2} ({planta} / {etiqueta}): la suma molar da {suma:.4f}. "
                "Se normaliza a 1, pero conviene revisar el dato de origen.")

        if abs(suma - 1.0) > 1e-9:
            cromato = cromato / suma

        cromas.setdefault(planta, []).append({
            "vol_derivacion": volumen * float(factor_volumen),
            "cromato_derivacion": cromato,
            "origen": etiqueta,
        })

    if not cromas:
        avisos.append("No quedo ninguna fila valida.")

    return cromas, avisos


def resumen(cromas: dict[str, list[dict]], factor_volumen=1000.0) -> pd.DataFrame:
    """Tabla chica para mostrar que se cargo, en MMm3/d."""
    filas = []
    for planta, entradas in cromas.items():
        for e in entradas:
            filas.append({
                "Planta": planta,
                "Origen": e["origen"],
                "Volumen [MMm3/d]": e["vol_derivacion"] / factor_volumen,
                "Suma molar": float(e["cromato_derivacion"].sum()),
            })
    return pd.DataFrame(filas)


def plantilla(compuestos, path="datos/cromas_planta_ejemplo.xlsx") -> str:
    """Genera un archivo vacio con las columnas correctas, para que el usuario
    no tenga que adivinar el formato."""
    columnas = [COL_PLANTA, COL_ORIGEN, COL_VOLUMEN] + list(compuestos)
    df = pd.DataFrame(columns=columnas)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False)
    return str(path)
