"""
Registro de plantas: la cascada como DATO, y `crear_planta` como unica puerta.
==============================================================================

Antes el orden TBX -> DP -> MEGA vivia escrito a mano en `main.py` y `app.py`, y
los topes en `config.py`. Aca la cascada es un dict `{nombre: PlantaConfig}` que
se arma desde la UI, se serializa a JSON y se vuelve a cargar.

UNA PLANTA ES UN JUEGO DE FEATURES
----------------------------------
No hay tipos de planta. `modelar_planta` es uno solo (ver planta.py) y lo que
distingue a MEGA de un tren TTY son los valores de tres features:

    MEGA        deriva=False, activa=True, toma_volumen_del_pool=True
    TTY-TBX     deriva=True (a DP, mismo pool), activa segun fecha de PM
    TTY-DP      deriva=True (a MEGA, otra composicion),
                toma_volumen_del_pool=False (el volumen se lo pasa TBX)

Los PRESETS de abajo son eso y nada mas: valores por defecto para esas features.
`crear_planta(nombre, preset="MEGA", ...)` devuelve una planta que se comporta
como MEGA porque tiene sus features, no porque haya un `if tipo == "MEGA"` en
algun lado.

DOS NOMBRES QUE SE PARECEN Y NO SON LO MISMO
--------------------------------------------
- `nombre`      : identificador de la instancia (clave del registro, lo que se
                  ve en la UI y en el grafo).
- `nombre_pool` : el valor de la columna `Gasoducto` con el que se filtra el gas
                  que entra. DOS PLANTAS CON EL MISMO `nombre_pool` SON DOS
                  TRENES SOBRE EL MISMO GAS, con cromatografia identica. Es
                  exactamente la relacion TBX / Dew Point, y es lo que permite
                  sumar un tercer tren sin tocar nada.

CONEXIONES POR PROPORCION
-------------------------
Cada planta declara a donde manda su SOBRANTE y en que proporcion. Reemplaza al
viejo `MAX_DERIVACION_*`, que era un unico destino con un tope absoluto: ahora
es un splitter, `tope` sigue existiendo como limite duro por rama y lo que no
toma nadie es bypass.

`comparte_pool=True` significa que origen y destino son trenes sobre el mismo
gas (TBX -> DP): la cromatografia es identica, solo se pasa volumen.
`False` es una derivacion real (DP -> MEGA): el gas entra a un pool de otra
composicion y tiene que pesar en la mezcla.

UNIDADES
--------
Todo lo volumetrico (capacidad_ingreso, topes) va en unidades de
`Volumen_inyectado`, igual que en config. La UI convierte desde MMm3/d.
`capacidad_evacuacion` va en tn/d.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pandas as pd


INFINITO = float("inf")


# ===========================================================================
# Estructuras
# ===========================================================================

@dataclass
class ConexionSalida:
    """Una rama de salida del sobrante de una planta."""

    destino: str
    proporcion: float = 1.0          # 0..1 del sobrante
    tope: float = INFINITO           # limite duro de la rama, en unidades de volumen
    comparte_pool: bool = False      # True = mismo gas (no se mezcla)

    def a_dict(self) -> dict:
        d = asdict(self)
        d["tope"] = None if self.tope == INFINITO else self.tope   # json no tiene inf
        return d

    @staticmethod
    def desde_dict(d: dict) -> "ConexionSalida":
        tope = d.get("tope")
        return ConexionSalida(
            destino=d["destino"],
            proporcion=float(d.get("proporcion", 1.0)),
            tope=INFINITO if tope is None else float(tope),
            comparte_pool=bool(d.get("comparte_pool", False)),
        )


@dataclass
class PlantaConfig:
    """Los features de una planta. Armala con `crear_planta`, no a mano."""

    nombre: str
    nombre_pool: str | None = None

    # Capacidades. La restriccion activa es la de evacuacion de LGN (tn/d); la
    # de ingreso entra solo como min() adicional y puede ser None (sin limite).
    capacidad_evacuacion: float = INFINITO
    capacidad_ingreso: float | None = None

    # Retencion por compuesto: DataFrame de UNA fila, columnas = COMPUESTOS,
    # valores en fraccion (0..1). Misma forma que
    # `retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == ...]`, asi entra
    # derecho a `io_plantas` sin adaptador.
    retenidos: pd.DataFrame | None = None

    conexiones: list[ConexionSalida] = field(default_factory=list)

    # Interruptor general de la derivacion. Apagado, la planta se comporta como
    # ultimo eslabon: todo el sobrante es bypass. Es un flag aparte y no "borrar
    # las conexiones" a proposito: deja comparar escenarios sin perder el
    # esquema de proporciones ya configurado.
    #
    # El bypass no tiene interruptor equivalente: siempre existe. Es el destino
    # de lo que ninguna planta pudo tratar ni recibir, asi que apagarlo seria
    # hacer desaparecer gas del balance.
    deriva: bool = True

    # True: arranca la cadena tomando todo su pool.
    # False: el volumen se lo pasa el eslabon anterior (caso TTY-DP).
    toma_volumen_del_pool: bool = True

    activa: bool = True
    color: str = "#EAF2F8"

    # Filas de cromatografia cargadas por archivo aparte (ver
    # io_.cromatografias_planta). Cada una es
    # {'vol_derivacion': float, 'cromato_derivacion': Series, 'origen': str}
    # y se inyecta al pool por el mismo mecanismo que una derivacion.
    cromas_extra: list[dict] = field(default_factory=list)

    # Marca de origen: las tres de siempre vs las que agrego el usuario.
    es_base: bool = False

    def destinos(self) -> list[str]:
        """Destinos EFECTIVOS. Con `deriva=False` no hay ninguno, aunque las
        conexiones sigan cargadas. Lo usan la validacion y el orden topologico,
        asi que apagar la derivacion tambien rompe un ciclo."""
        return [c.destino for c in self.conexiones] if self.deriva else []

    def a_dict(self) -> dict:
        """Version serializable, INCLUIDAS las cromatografias cargadas a mano.

        Al principio las dejaba afuera (son un archivo aparte que se vuelve a
        subir), pero eso obliga a repetir dos pasos manuales cada vez que se
        recupera un escenario. Un escenario guardado tiene que poder volver
        entero de un click.
        """
        return {
            "nombre": self.nombre,
            "nombre_pool": self.nombre_pool,
            "capacidad_evacuacion": (
                None if self.capacidad_evacuacion == INFINITO else self.capacidad_evacuacion),
            "capacidad_ingreso": self.capacidad_ingreso,
            "retenidos": (
                None if self.retenidos is None
                else self.retenidos.iloc[0].astype(float).to_dict()),
            "conexiones": [c.a_dict() for c in self.conexiones],
            "deriva": self.deriva,
            "toma_volumen_del_pool": self.toma_volumen_del_pool,
            "activa": self.activa,
            "color": self.color,
            "es_base": self.es_base,
            "cromas_extra": [
                {
                    "vol_derivacion": float(c["vol_derivacion"]),
                    "origen": str(c.get("origen", "")),
                    # La cromato es una Series indexada por compuesto; a dict y
                    # de vuelta, para no depender del formato binario de pandas.
                    "cromato_derivacion": {
                        str(k): float(v)
                        for k, v in dict(c["cromato_derivacion"]).items()},
                }
                for c in (self.cromas_extra or [])
            ],
        }

    @staticmethod
    def desde_dict(d: dict) -> "PlantaConfig":
        cap_evac = d.get("capacidad_evacuacion")
        ret = d.get("retenidos")
        return PlantaConfig(
            nombre=d["nombre"],
            nombre_pool=d.get("nombre_pool"),
            capacidad_evacuacion=INFINITO if cap_evac is None else float(cap_evac),
            capacidad_ingreso=(
                None if d.get("capacidad_ingreso") is None else float(d["capacidad_ingreso"])),
            retenidos=None if ret is None else pd.DataFrame([ret]),
            conexiones=[ConexionSalida.desde_dict(c) for c in d.get("conexiones", [])],
            deriva=bool(d.get("deriva", True)),
            toma_volumen_del_pool=bool(d.get("toma_volumen_del_pool", True)),
            activa=bool(d.get("activa", True)),
            color=d.get("color", "#EAF2F8"),
            es_base=bool(d.get("es_base", False)),
            cromas_extra=[
                {
                    "vol_derivacion": float(c["vol_derivacion"]),
                    "origen": c.get("origen", ""),
                    "cromato_derivacion": pd.Series(
                        c["cromato_derivacion"], dtype="float64"),
                }
                for c in d.get("cromas_extra", [])
            ],
        )


# ===========================================================================
# Presets: features, no tipos
# ===========================================================================

# Cada preset es el juego de features con el que un modelo conocido se comporta
# como el mismo. No hay codigo detras: son defaults para `crear_planta`.
PRESETS = {
    # Terminal: no deriva, siempre en servicio, siempre toma su pool. Es la
    # combinacion con la que `modelar_planta` reproduce `modelar_MEGA`.
    "MEGA": {
        "deriva": False,
        "activa": True,
        "toma_volumen_del_pool": True,
        "color": "#F5B041",
    },
    # Tren: deriva el sobrante y se puede apagar. Cabecera del pool.
    "TTY": {
        "deriva": True,
        "activa": True,
        "toma_volumen_del_pool": True,
        "color": "#5DADE2",
    },
    # Tren aguas abajo de otro sobre el MISMO pool: el volumen se lo pasa el
    # anterior, el pool solo aporta la cromatografia. Es TTY-Dew Point.
    "TTY_TREN_SIGUIENTE": {
        "deriva": True,
        "activa": True,
        "toma_volumen_del_pool": False,
        "color": "#48C9B0",
    },
}


def crear_planta(nombre, preset=None, compuestos=None, nombre_pool=None,
                 retenidos=None, capacidad_evacuacion=None, capacidad_ingreso=None,
                 conexiones=None, color=None, es_base=False, **features) -> PlantaConfig:
    """Unica puerta de entrada para armar una planta.

    Parameters
    ----------
    nombre : str
        Identificador de la instancia.
    preset : str | None
        Clave de `PRESETS`. Solo aporta DEFAULTS para las features; cualquiera
        se puede pisar por kwarg. `crear_planta("X", preset="MEGA")` da una
        planta que se comporta como MEGA; `crear_planta("X", preset="MEGA",
        deriva=True)` da una MEGA que ademas deriva, y es legal, porque el
        modelo es uno solo.
    compuestos : list | None
        Para inicializar los retenidos en cero si no se pasan. Sin datos
        cargados no retiene nada, que es lo unico honesto.
    nombre_pool : str | None
        Valor de `Gasoducto` con el que filtra su gas. None = igual al nombre.
        Ponele el de otra planta para armar un tren sobre el mismo pool.
    retenidos : DataFrame | dict | Series | None
        Fraccion retenida por compuesto. Se normaliza a DataFrame de una fila.

    Raises
    ------
    ValueError
        Nombre vacio, preset desconocido, o retenidos sin forma reconocible.
        Se falla temprano: una planta mal armada da numeros mal en silencio.
    """

    nombre = str(nombre or "").strip()
    if not nombre:
        raise ValueError("La planta necesita un nombre.")

    if preset is not None and preset not in PRESETS:
        raise ValueError(
            f"Preset '{preset}' desconocido. Disponibles: {', '.join(PRESETS)}.")

    base = dict(PRESETS.get(preset, {})) if preset else {}
    base.update(features)

    desconocidas = set(base) - {
        "deriva", "activa", "toma_volumen_del_pool", "color"}
    if desconocidas:
        raise ValueError(
            f"Features desconocidas para '{nombre}': {', '.join(sorted(desconocidas))}.")

    if color is not None:
        base["color"] = color

    return PlantaConfig(
        nombre=nombre,
        nombre_pool=(nombre_pool or nombre),
        capacidad_evacuacion=(
            INFINITO if capacidad_evacuacion is None else float(capacidad_evacuacion)),
        capacidad_ingreso=(
            None if capacidad_ingreso is None else float(capacidad_ingreso)),
        retenidos=_normalizar_retenidos(retenidos, compuestos, nombre),
        conexiones=list(conexiones or []),
        es_base=es_base,
        **base,
    )


def _normalizar_retenidos(retenidos, compuestos, nombre) -> pd.DataFrame | None:
    """Acepta DataFrame de una fila, dict o Series y devuelve siempre el
    DataFrame de una fila que espera `io_plantas`."""

    if retenidos is None:
        if compuestos is None:
            return None
        return pd.DataFrame([{c: 0.0 for c in compuestos}])

    if isinstance(retenidos, pd.DataFrame):
        if len(retenidos) != 1:
            raise ValueError(
                f"Los retenidos de '{nombre}' tienen {len(retenidos)} filas; "
                "tiene que ser exactamente una (columnas = compuestos).")
        return retenidos.copy()

    if isinstance(retenidos, pd.Series):
        return retenidos.to_frame().T

    if isinstance(retenidos, dict):
        return pd.DataFrame([retenidos])

    raise ValueError(
        f"No se entienden los retenidos de '{nombre}': "
        f"se esperaba DataFrame de una fila, Series o dict, llego {type(retenidos).__name__}.")


# ===========================================================================
# Registro base: las tres de siempre, armadas con crear_planta
# ===========================================================================

def _leer(fuente, clave):
    """Lee una constante tanto de `config` (modulo) como de `params` (dict).

    En `app.py` los valores que el usuario toca en la sidebar viven en el dict
    `params`; en `main.py` viven como atributos de `config`. Aceptar los dos
    evita tener que decidir cual es la fuente de verdad desde este lado.
    """
    if isinstance(fuente, dict):
        return fuente[clave]
    return getattr(fuente, clave)


def registro_base(config, retenidos_rtp, compuestos, tbx_en_servicio: bool) -> dict[str, PlantaConfig]:
    """Reconstruye la cascada actual con la estructura nueva.

    `config` puede ser el modulo o el dict `params` de la sidebar de app.py.

    Corriendo esto con los mismos inputs, el resultado tiene que dar IDENTICO al
    de `main.py`. Es la prueba de que el refactor no cambio el modelo:

      - TBX es cabecera del pool TTY y manda el sobrante a DP con
        `comparte_pool=True`, tope MAX_DERIVACION_TTY_TBX_A_TTY_DP.
      - Pre-PM, TBX esta inactiva: no trata nada y sus topes se ignoran, asi que
        todo el pool cae en DP. Igual que antes con el `float('inf')`.
      - DP no toma volumen del pool (se lo pasa TBX) y deriva a MEGA con
        `comparte_pool=False`, tope MAX_DERIVACION_TTY_DP_A_MEGA.
      - MEGA es cabecera de su propio pool y no deriva: todo su sobrante es
        bypass.
    """

    def _ret(clave):
        return retenidos_rtp[retenidos_rtp["Planta"] == clave][list(compuestos)]

    tbx = crear_planta(
        "TTY - TBX", preset="TTY", nombre_pool="TTY",
        retenidos=_ret("TBX"),
        capacidad_evacuacion=_leer(config, "CAPACIDAD_EVACUACION_TTY_TBX"),
        capacidad_ingreso=_leer(config, "CAPACIDAD_TTY_TBX"),
        conexiones=[ConexionSalida(
            destino="TTY - Dew Point", proporcion=1.0,
            tope=_leer(config, "MAX_DERIVACION_TTY_TBX_A_TTY_DP"), comparte_pool=True)],
        activa=tbx_en_servicio,
        es_base=True,
    )

    dp = crear_planta(
        "TTY - Dew Point", preset="TTY_TREN_SIGUIENTE", nombre_pool="TTY",
        retenidos=_ret("Dew point"),
        capacidad_evacuacion=_leer(config, "CAPACIDAD_EVACUACION_TTY_DP"),
        capacidad_ingreso=_leer(config, "CAPACIDAD_TTY_DP"),
        conexiones=[ConexionSalida(
            destino="MEGA", proporcion=1.0,
            tope=_leer(config, "MAX_DERIVACION_TTY_DP_A_MEGA"), comparte_pool=False)],
        es_base=True,
    )

    mega = crear_planta(
        "MEGA", preset="MEGA", nombre_pool="MEGA",
        retenidos=_ret("TBX MEGA"),
        capacidad_evacuacion=_leer(config, "CAPACIDAD_EVACUACION_MEGA"),
        capacidad_ingreso=_leer(config, "CAPACIDAD_MEGA"),
        es_base=True,
    )

    return {p.nombre: p for p in (tbx, dp, mega)}


# ===========================================================================
# Validacion
# ===========================================================================

def validar_registro(registro: dict[str, PlantaConfig]) -> tuple[list[str], list[str]]:
    """(errores, advertencias).

    ERRORES bloquean la corrida: destino inexistente, ciclo, autoconexion,
    planta sin fuente de gas. ADVERTENCIAS son cosas raras pero corribles.
    """
    errores: list[str] = []
    avisos: list[str] = []

    for nombre, p in registro.items():
        if not p.deriva and p.conexiones:
            avisos.append(
                f"'{nombre}' tiene la derivacion apagada: sus {len(p.conexiones)} "
                "conexion(es) quedan guardadas pero no mueven gas, y todo el "
                "sobrante va a bypass.")

        # Con la derivacion apagada las conexiones no se validan como errores
        # duros: estan ahi de adorno hasta que se prenda.
        for c in (p.conexiones if p.deriva else []):
            if c.destino == nombre:
                errores.append(f"'{nombre}' se conecta a si misma.")
            elif c.destino not in registro:
                errores.append(
                    f"'{nombre}' deriva a '{c.destino}', que no existe en el registro.")
            if c.proporcion < 0:
                errores.append(f"Proporcion negativa en {nombre} -> {c.destino}.")

        suma = sum(max(c.proporcion, 0.0) for c in p.conexiones)
        if p.deriva and p.conexiones and suma > 1.0 + 1e-9:
            avisos.append(
                f"Las proporciones de salida de '{nombre}' suman {suma:.0%}. "
                "Se renormalizan a 100% del sobrante.")

        if not p.toma_volumen_del_pool and not _tiene_entrada(nombre, registro):
            errores.append(
                f"'{nombre}' no toma volumen de su pool y nadie le deriva gas: "
                "queda en cero. Marcala como cabecera o conectale un origen.")

        if p.retenidos is None:
            errores.append(f"'{nombre}' no tiene retenidos cargados.")
        else:
            vals = p.retenidos.iloc[0].astype(float)
            fuera = vals[(vals < 0) | (vals > 1)]
            if len(fuera):
                avisos.append(
                    f"'{nombre}' tiene retenidos fuera de 0-100% en: "
                    f"{', '.join(map(str, fuera.index[:5]))}.")

    try:
        orden_topologico(registro)
    except ValueError as e:
        errores.append(str(e))

    return errores, avisos


def _tiene_entrada(nombre: str, registro: dict[str, PlantaConfig]) -> bool:
    return any(nombre in p.destinos() for p in registro.values())


def orden_topologico(registro: dict[str, PlantaConfig]) -> list[str]:
    """Orden en que hay que resolver las plantas: cada una despues de todas las
    que le mandan gas. Kahn clasico; si queda algo sin visitar, hay un ciclo."""

    grado = {n: 0 for n in registro}
    for p in registro.values():
        for destino in p.destinos():
            if destino in grado:
                grado[destino] += 1

    # Cola ordenada para que el resultado sea determinista y no dependa del
    # orden de insercion del dict.
    cola = sorted([n for n, g in grado.items() if g == 0])
    orden: list[str] = []

    while cola:
        nombre = cola.pop(0)
        orden.append(nombre)
        for destino in registro[nombre].destinos():
            if destino not in grado:
                continue
            grado[destino] -= 1
            if grado[destino] == 0:
                cola.append(destino)
                cola.sort()

    if len(orden) != len(registro):
        faltan = sorted(set(registro) - set(orden))
        raise ValueError(
            f"Hay un ciclo en las conexiones: {', '.join(faltan)}. "
            "El gas no puede volver a una planta anterior de la cascada.")

    return orden


# ===========================================================================
# Persistencia
# ===========================================================================

def guardar_registro(registro: dict[str, PlantaConfig], path="datos/plantas.json") -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    datos = [p.a_dict() for p in registro.values()]
    path.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def cargar_registro(path="datos/plantas.json") -> dict[str, PlantaConfig]:
    datos = json.loads(Path(path).read_text(encoding="utf-8"))
    plantas = [PlantaConfig.desde_dict(d) for d in datos]
    return {p.nombre: p for p in plantas}
