"""
Altas y bajas de gasoductos, con redistribucion proporcional del volumen.
=========================================================================

Que hace
--------
Modifica las tablas de entrada de la cascada ANTES de resolverla, para poder
preguntarse "que pasa si abro un ducto de tal area a tal planta" o "que pasa si
saco tal ducto por mantenimiento". No toca el pipeline: recibe las tablas ya
calculadas, devuelve copias modificadas.

EL INVARIANTE
-------------
El volumen que inyecta cada AREA no cambia nunca. Un gasoducto no crea ni
destruye gas: solo cambia por donde sale. Entonces toda intervencion es una
redistribucion dentro del area, y

    sum(Volumen_inyectado del area) == igual antes y despues

Ese invariante es lo que hace que la comparacion contra la corrida oficial tenga
sentido: si cambia el total inyectado, la diferencia que se ve en las plantas ya
no es por el ducto sino por gas que aparecio o se perdio.

ALTA
----
Un area A inyecta hoy T MMm3/d repartidos entre destinos {d1: v1, d2: v2, ...}.
Se abre un ducto nuevo n con volumen V (con V <= T, porque no puede mandar mas
gas del que el area produce). El resto R = T - V se reparte entre los destinos
que ya estaban, en la MISMA proporcion en la que estaban:

    vi' = vi * R / T        y      vn = V

Se agregan dos filas, igual que un ducto real:

    yacimientos      Area -> n           (el gas sale del area al ducto)
    flujos directos  n    -> Planta      (el ducto entrega en la planta)

La de flujos directos es la que hace que la planta lo vea: `armar_input_planta`
filtra por `Gasoducto == nombre_planta`. La de yacimientos es la que lo hace
aparecer en el mapa y en `red_gasoductos`.

BAJA (mantenimiento)
--------------------
Un ducto k sale de servicio. Para CADA area que le inyectaba, su volumen vk se
reparte entre los otros destinos de esa area, proporcional a como estaban:

    vi' = vi * T / (T - vk)

Como por ahora los ductos no tienen capacidad maxima, el gas siempre entra: la
baja no genera bypass, solo mueve gas de un lado a otro. Cuando haya capacidades
esto cambia, y ahi la baja empieza a tener consecuencias interesantes.

CASO SIN SALIDA
---------------
Si un area inyecta UNICAMENTE al ducto que se da de baja, no hay a donde mover
su gas. No se inventa un destino: esas filas se dejan como estan y se reportan
en el informe. Repartirlas por default seria decidir por el usuario algo que el
modelo no sabe.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import pandas as pd


COL_AREA = "Area"
COL_DESTINO = "Gasoducto"
COL_VOLUMEN = "Volumen_inyectado"

# `tabla_total.py` agrega, por cada compuesto, una columna
# `Vol_<compuesto> = <compuesto> * Volumen_inyectado`. Son EXTENSIVAS: dependen
# del volumen, no solo de la composicion. Cualquier cosa que mueva
# `Volumen_inyectado` las deja desincronizadas, y como nadie las mira de cerca
# el error no se nota. Por eso se recalculan siempre al final.
#
# Las otras derivadas que agrega `calcular_propiedades_gas` (z, densidad, PCS,
# IW) son INTENSIVAS: salen de las fracciones molares y no cambian al tomar una
# porcion del mismo gas. Esas no hay que tocarlas.
PREFIJO_VOL = "Vol_"

_EPS = 1e-9


# ===========================================================================
# Nombres: dos convenciones conviviendo
# ===========================================================================
#
# El pipeline usa DOS formas del mismo nombre y hay que respetar cual va en cada
# columna, o el gas del ducto nuevo no llega a ningun lado:
#
#   tabla_total_yacimientos['Gasoducto']     CRUDA        "VMN"
#       Sale de los nombres de COLUMNA de la matriz, que no pasan por normalizar.
#
#   tabla_total_flujos_directos['Area']      NORMALIZADA  "vmn"
#       Pasa por `normalizar()`.
#
#   matriz_inyecciones[planta]  (ancha)      CRUDA
#       `io_plantas` le aplica `normalizar` antes de mergear contra
#       flujos_directos, con `how='inner'`. O sea que la matriz FILTRA: si el
#       ducto nuevo no figura ahi, su fila se descarta en silencio.
#
# Por eso todo el matcheo de este modulo va por `_clave`, y al escribir un
# nombre nuevo se usa `_en_la_forma_de`, que deduce de la propia columna si
# espera la version cruda o la normalizada en vez de asumirlo.


def _clave(texto) -> str:
    """Misma regla que `domain.normalizacion.normalizar`.

    Se replica en vez de importarla para que este modulo se pueda testear sin
    arrastrar el dominio entero. Si alla cambia la regla, hay que tocar aca.
    """
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""

    texto = str(texto).strip().lower()
    texto = "".join(c for c in unicodedata.normalize("NFD", texto)
                    if unicodedata.category(c) != "Mn")

    return "".join(c for c in texto if c.isalnum())


def _columna_normalizada(serie, muestra=100) -> bool:
    """True si los valores de la columna ya vienen normalizados."""
    valores = serie.dropna().astype(str).head(muestra)
    if valores.empty:
        return False
    return all(v == _clave(v) for v in valores)


def _en_la_forma_de(serie, nombre) -> str:
    """Devuelve `nombre` en la misma forma que usa esa columna."""
    return _clave(nombre) if _columna_normalizada(serie) else str(nombre)


def _mascara_clave(serie, nombre):
    """Compara por clave: sirve para columnas crudas y normalizadas por igual."""
    objetivo = _clave(nombre)
    return serie.map(_clave) == objetivo


# ===========================================================================
# Estructuras
# ===========================================================================

@dataclass
class Intervencion:
    """Un alta o una baja de gasoducto."""

    tipo: str                      # "alta" | "baja"
    nombre: str                    # nombre del ducto

    # Solo para alta:
    area_origen: str | None = None
    planta_destino: str | None = None
    volumen: float = 0.0           # en unidades de Volumen_inyectado
    cromato: pd.Series | None = None   # fraccion molar por compuesto

    # Alternativa a `volumen`, para escenarios PORTABLES. Si esta seteada, el
    # volumen se calcula como esta fraccion del total que inyecta el area.
    # Un escenario de ejemplo no puede hardcodear 250 unidades: depende de los
    # datos de cada uno. "el 30% de lo que inyecta el area" si es portable.
    fraccion: float | None = None

    activa: bool = True

    def a_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "nombre": self.nombre,
            "area_origen": self.area_origen,
            "planta_destino": self.planta_destino,
            "volumen": float(self.volumen),
            "fraccion": self.fraccion,
            "activa": self.activa,
            "cromato": (None if self.cromato is None
                        else {str(k): float(v) for k, v in dict(self.cromato).items()}),
        }

    @staticmethod
    def desde_dict(d: dict) -> "Intervencion":
        cromato = d.get("cromato")
        return Intervencion(
            tipo=d["tipo"],
            nombre=d["nombre"],
            area_origen=d.get("area_origen"),
            planta_destino=d.get("planta_destino"),
            volumen=float(d.get("volumen", 0.0)),
            fraccion=(None if d.get("fraccion") is None else float(d["fraccion"])),
            activa=bool(d.get("activa", True)),
            cromato=None if cromato is None else pd.Series(cromato, dtype="float64"),
        )


@dataclass
class Informe:
    """Que paso al aplicar las intervenciones. Va entero a la UI."""

    cambios: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)

    def tabla(self) -> pd.DataFrame:
        return pd.DataFrame(self.cambios)


# ===========================================================================
# Consultas sobre las tablas
# ===========================================================================

def areas_disponibles(tabla_yacimientos) -> list[str]:
    """Areas que inyectan algo. Ordenadas por volumen, de mayor a menor: en un
    desplegable de ~130 items, las que importan tienen que estar arriba."""
    if tabla_yacimientos is None or tabla_yacimientos.empty:
        return []
    por_area = (tabla_yacimientos.groupby(COL_AREA)[COL_VOLUMEN]
                .sum().sort_values(ascending=False))
    return [str(a) for a in por_area.index]


def volumen_area(tabla_yacimientos, area) -> float:
    """Total que inyecta un area. Es el tope de un alta."""
    if tabla_yacimientos is None or tabla_yacimientos.empty:
        return 0.0
    filas = tabla_yacimientos[tabla_yacimientos[COL_AREA] == area]
    return float(filas[COL_VOLUMEN].sum())


def destinos_area(tabla_yacimientos, area) -> pd.Series:
    """{destino: volumen} de un area, para mostrar el reparto actual."""
    if tabla_yacimientos is None or tabla_yacimientos.empty:
        return pd.Series(dtype="float64")
    filas = tabla_yacimientos[tabla_yacimientos[COL_AREA] == area]
    return filas.groupby(COL_DESTINO)[COL_VOLUMEN].sum().sort_values(ascending=False)


def gasoductos_disponibles(tabla_yacimientos, tabla_flujos_directos) -> list[str]:
    """Destinos que son ductos (aparecen como `Area` en flujos directos).

    Un destino que NO aparece como origen en flujos directos es una planta que
    recibe directo del area, y dar de baja eso no es "sacar un ducto".
    """
    if tabla_yacimientos is None or tabla_flujos_directos is None:
        return []

    # La interseccion va por clave: en yacimientos el destino es "VMN" y en
    # flujos directos el origen es "vmn". Comparando los strings crudos el
    # resultado seria SIEMPRE vacio y el desplegable de baja quedaria sin nada.
    claves_fd = {_clave(a) for a in tabla_flujos_directos[COL_AREA].dropna()}

    vistos, salida = set(), []
    for destino in tabla_yacimientos[COL_DESTINO].dropna().astype(str):
        k = _clave(destino)
        if k in claves_fd and k not in vistos:
            vistos.add(k)
            # Se devuelve la forma de yacimientos, que es la que el usuario
            # reconoce ("VMN", no "vmn").
            salida.append(destino)

    return sorted(salida)


# ===========================================================================
# Referencias relativas, para escenarios portables
# ===========================================================================
#
# Un escenario de ejemplo no puede nombrar areas ni ductos concretos: los
# nombres dependen de los datos de cada uno. Dos formas de referirse a algo sin
# saber como se llama:
#
#   area_origen = "#1"    la primera area por volumen inyectado, "#2" la
#                         segunda, y asi. `areas_disponibles` ya las devuelve
#                         ordenadas de mayor a menor.
#   nombre      = "#1"    (solo en bajas) el ducto que mas volumen transporta.
#   fraccion    = 0.3     en vez de `volumen`: el 30% de lo que inyecta el area.
#
# Se resuelven UNA vez, al principio de `aplicar_intervenciones`, y a partir de
# ahi el resto del modulo trabaja con nombres y volumenes concretos.


def _es_referencia(valor) -> bool:
    return isinstance(valor, str) and valor.startswith("#") and valor[1:].isdigit()


def _resolver(intervencion, yac, fdi, informe):
    """Devuelve una copia con las referencias relativas ya resueltas."""
    from dataclasses import replace

    cambios = {}

    if _es_referencia(intervencion.area_origen):
        indice = int(intervencion.area_origen[1:]) - 1
        areas = areas_disponibles(yac)
        if indice < 0 or indice >= len(areas):
            informe.errores.append(
                f"'{intervencion.nombre}': la referencia "
                f"'{intervencion.area_origen}' pide el área número "
                f"{indice + 1} y sólo hay {len(areas)}.")
            return None
        cambios["area_origen"] = areas[indice]

    if intervencion.tipo == "baja" and _es_referencia(intervencion.nombre):
        indice = int(intervencion.nombre[1:]) - 1
        ductos = _ductos_por_volumen(yac, fdi)
        if indice < 0 or indice >= len(ductos):
            informe.errores.append(
                f"La referencia '{intervencion.nombre}' pide el ducto número "
                f"{indice + 1} y sólo hay {len(ductos)}.")
            return None
        cambios["nombre"] = ductos[indice]

    resuelta = replace(intervencion, **cambios) if cambios else intervencion

    if resuelta.fraccion is not None and resuelta.tipo == "alta":
        total = volumen_area(yac, resuelta.area_origen)
        resuelta = replace(resuelta, volumen=total * float(resuelta.fraccion))

    return resuelta


def _ductos_por_volumen(yac, fdi) -> list[str]:
    """Ductos ordenados por volumen transportado, de mayor a menor."""
    ductos = gasoductos_disponibles(yac, fdi)
    if not ductos:
        return []

    claves = {_clave(d): d for d in ductos}
    por_clave = (yac.assign(_k=yac[COL_DESTINO].map(_clave))
                 .groupby("_k")[COL_VOLUMEN].sum())

    presentes = [(por_clave.get(k, 0.0), nombre) for k, nombre in claves.items()]
    presentes.sort(reverse=True)

    return [nombre for _, nombre in presentes]


# ===========================================================================
# Aplicacion
# ===========================================================================

def aplicar_intervenciones(tabla_yacimientos, tabla_flujos_directos,
                           intervenciones, compuestos, matriz_inyecciones=None):
    """
    Returns
    -------
    (yacimientos, flujos_directos, matriz, informe)
        Copias modificadas. Los originales no se tocan.
    """
    informe = Informe()

    yac = tabla_yacimientos.copy() if tabla_yacimientos is not None else None
    fdi = tabla_flujos_directos.copy() if tabla_flujos_directos is not None else None
    matriz = matriz_inyecciones.copy() if matriz_inyecciones is not None else None

    activas = [i for i in intervenciones if i.activa]
    if not activas:
        return yac, fdi, matriz, informe

    # Las referencias relativas ("#1", `fraccion`) se resuelven contra las
    # tablas ANTES de tocar nada: si se resolvieran sobre la marcha, un "#1"
    # aplicado despues de una baja apuntaria a otra area.
    resueltas = []
    for intervencion in activas:
        concreta = _resolver(intervencion, yac, fdi, informe)
        if concreta is not None:
            resueltas.append(concreta)

    activas = resueltas

    # Las BAJAS primero: si un alta manda gas a un ducto que despues se da de
    # baja, el resultado depende del orden. Bajas antes deja el estado "ducto
    # fuera de servicio" y despues el alta se reparte sobre lo que quedo, que es
    # el orden en que uno lo pensaria.
    for intervencion in [i for i in activas if i.tipo == "baja"]:
        yac, fdi = _baja(yac, fdi, intervencion, informe)

    for intervencion in [i for i in activas if i.tipo == "alta"]:
        yac, fdi, matriz = _alta(yac, fdi, matriz, intervencion, compuestos, informe)

    # Los ductos son agregados: si cambio el gas que les entra, hay que
    # cambiar el que entregan. Se compara contra el estado ORIGINAL, no contra
    # el intermedio, para que varias intervenciones sobre la misma area no
    # apliquen el factor dos veces.
    fdi = _propagar_a_flujos_directos(tabla_yacimientos, yac, fdi, informe)

    # Ultimo paso, siempre: las `Vol_<compuesto>` son extensivas y quedaron
    # viejas en cada fila cuyo volumen se movio. Ver el comentario de
    # PREFIJO_VOL.
    yac = _recalcular_vol_compuestos(yac, compuestos)
    fdi = _recalcular_vol_compuestos(fdi, compuestos)

    return yac, fdi, matriz, informe


# ---------------------------------------------------------------------------

def _alta(yac, fdi, matriz, intervencion, compuestos, informe):
    area = intervencion.area_origen
    nombre = intervencion.nombre
    planta = intervencion.planta_destino

    if yac is None or fdi is None:
        informe.errores.append(f"'{nombre}': faltan las tablas de entrada.")
        return yac, fdi, matriz

    mascara_area = yac[COL_AREA] == area
    if not mascara_area.any():
        informe.errores.append(
            f"'{nombre}': el área '{area}' no inyecta en ningún destino.")
        return yac, fdi, matriz

    if _mascara_clave(yac[COL_DESTINO], nombre).any():
        informe.errores.append(
            f"'{nombre}': ya existe un destino con ese nombre. Elegí otro.")
        return yac, fdi, matriz

    total = float(yac.loc[mascara_area, COL_VOLUMEN].sum())
    volumen = float(intervencion.volumen)

    if volumen > total + _EPS:
        informe.avisos.append(
            f"'{nombre}': pediste {volumen:,.0f} pero '{area}' inyecta "
            f"{total:,.0f}. Se recorta al total del área.")
        volumen = total

    volumen = max(volumen, 0.0)
    restante = total - volumen

    # Reparto proporcional del resto entre los destinos que ya estaban.
    # Si el ducto nuevo se lleva TODO, los demas quedan en cero pero las filas
    # se conservan: borrarlas perderia la cromatografia de esas rutas, y basta
    # bajar el volumen del ducto nuevo para que vuelvan.
    factor = (restante / total) if total > _EPS else 0.0
    yac.loc[mascara_area, COL_VOLUMEN] = yac.loc[mascara_area, COL_VOLUMEN] * factor

    # La fila nueva se clona de una existente del area para heredar HUB y
    # cualquier otra columna del esquema, y despues se pisan los tres campos que
    # cambian. Asi no hay que saber que columnas tiene la tabla.
    plantilla = yac.loc[mascara_area].iloc[0].copy()
    plantilla[COL_DESTINO] = _en_la_forma_de(yac[COL_DESTINO], nombre)
    plantilla[COL_VOLUMEN] = volumen
    _pisar_cromato(plantilla, intervencion.cromato, compuestos)

    yac = pd.concat([yac, plantilla.to_frame().T], ignore_index=True)

    # Segunda fila: el ducto entrega en la planta. Es la que hace que
    # `armar_input_planta` lo vea, porque filtra por `Gasoducto == planta`.
    fila_fd = _plantilla_flujo_directo(fdi, planta, compuestos)
    if fila_fd is None:
        informe.errores.append(
            f"'{nombre}': la tabla de flujos directos no tiene columnas, así "
            "que no se puede armar el tramo hacia la planta.")
        return yac, fdi, matriz

    # Aca va la OTRA forma: `Area` de flujos directos suele estar normalizada,
    # y es contra esta columna que `io_plantas` mergea la matriz normalizada.
    fila_fd[COL_AREA] = _en_la_forma_de(fdi[COL_AREA], nombre)
    fila_fd[COL_DESTINO] = planta
    fila_fd[COL_VOLUMEN] = volumen
    _pisar_cromato(fila_fd, intervencion.cromato, compuestos)

    fdi = pd.concat([fdi, fila_fd.to_frame().T], ignore_index=True)

    matriz = _declarar_en_matriz(matriz, planta, nombre, informe)

    informe.cambios.append({
        "Intervención": "alta",
        "Gasoducto": nombre,
        "Área": area,
        "Destino": planta,
        "Volumen": volumen,
        "Detalle": (f"el área inyecta {total:,.0f}; "
                    f"los otros destinos se reescalan a {factor:.1%}"),
    })

    return yac, fdi, matriz


def _baja(yac, fdi, intervencion, informe):
    nombre = intervencion.nombre

    if yac is None or fdi is None:
        informe.errores.append(f"'{nombre}': faltan las tablas de entrada.")
        return yac, fdi

    entra = _mascara_clave(yac[COL_DESTINO], nombre)
    if not entra.any():
        informe.avisos.append(
            f"'{nombre}': ningún área le inyecta, la baja no cambia nada.")

    movido = 0.0
    huerfanas = []

    for area in sorted(set(yac.loc[entra, COL_AREA])):
        del_area = yac[COL_AREA] == area
        al_ducto = del_area & entra
        a_otros = del_area & ~entra

        vol_ducto = float(yac.loc[al_ducto, COL_VOLUMEN].sum())
        vol_otros = float(yac.loc[a_otros, COL_VOLUMEN].sum())

        if vol_ducto <= _EPS:
            continue

        if vol_otros <= _EPS:
            # El area no tiene otra salida. No se inventa un destino: se deja
            # como esta y se reporta. Repartir por default seria decidir por el
            # usuario algo que el modelo no sabe.
            huerfanas.append((area, vol_ducto))
            continue

        # vi' = vi * (vol_otros + vol_ducto) / vol_otros
        yac.loc[a_otros, COL_VOLUMEN] = (
            yac.loc[a_otros, COL_VOLUMEN] * (vol_otros + vol_ducto) / vol_otros)
        yac.loc[al_ducto, COL_VOLUMEN] = 0.0
        movido += vol_ducto

    # Las filas del ducto se sacan de las dos tablas. Ya estan en cero en
    # yacimientos, pero dejarlas ensuciaria el mapa con una arista de volumen 0.
    yac = yac[~_mascara_clave(yac[COL_DESTINO], nombre)].copy()
    fdi = fdi[~_mascara_clave(fdi[COL_AREA], nombre)].copy()

    if huerfanas:
        detalle = ", ".join(f"{a} ({v:,.0f})" for a, v in huerfanas)
        informe.avisos.append(
            f"'{nombre}' fuera de servicio: {len(huerfanas)} área(s) no tienen "
            f"otro destino, así que ese gas queda sin ruta y NO se redistribuye: "
            f"{detalle}. El total inyectado baja en esa cantidad.")

    informe.cambios.append({
        "Intervención": "baja",
        "Gasoducto": nombre,
        "Área": f"{len(set(yac[COL_AREA]))} áreas afectadas" if movido else "—",
        "Destino": "—",
        "Volumen": movido,
        "Detalle": (f"{movido:,.0f} redistribuidos proporcionalmente"
                    + (f"; {len(huerfanas)} área(s) sin alternativa" if huerfanas else "")),
    })

    return yac, fdi


# ---------------------------------------------------------------------------

def _recalcular_vol_compuestos(df, compuestos):
    """Recalcula las columnas `Vol_<compuesto>` sobre el volumen actual.

    Se llama despues de TODA modificacion de volumen. Si no, un area a la que
    se le reescalo el volumen queda con los `Vol_*` del reparto anterior, y
    cualquier agregado que los use (energia por compuesto, LGN por corte) da un
    numero que no se corresponde con ninguna corrida.
    """
    if df is None or df.empty or COL_VOLUMEN not in df.columns:
        return df

    presentes = [c for c in compuestos
                 if c in df.columns and f"{PREFIJO_VOL}{c}" in df.columns]

    if not presentes:
        return df

    volumen = pd.to_numeric(df[COL_VOLUMEN], errors="coerce").fillna(0)

    for compuesto in presentes:
        fraccion = pd.to_numeric(df[compuesto], errors="coerce").fillna(0)
        df[f"{PREFIJO_VOL}{compuesto}"] = fraccion * volumen

    return df


def _propagar_a_flujos_directos(yac_antes, yac_despues, fdi, informe):
    """Ajusta las filas de flujos directos cuyo ORIGEN es un ducto que cambio.

    Por que hace falta. `armar_input_planta` lo dice: "el aporte de un area via
    un gasoducto ya viene agregado dentro de la fila de ese gasoducto". O sea
    que flujos directos tiene filas como (ypfrdm, MEGA): el ducto YPF-RDM como
    origen, con el total que entrega.

    Ese total es un AGREGADO independiente. Si una intervencion le baja el
    volumen a un area que inyectaba a VMN, la fila (vmn, TTY) sigue con el
    numero viejo y el pool de TTY termina recibiendo el VMN completo MAS el
    ducto nuevo: el gas se duplica. El invariante por area seguia cerrando, pero
    el de la planta no.

    Regla: para cada destino que ademas es origen en flujos directos, se escala
    su entrega por

        factor = volumen que le entra AHORA / volumen que le entraba ANTES

    calculado sobre yacimientos. Menos gas entrando al ducto es menos gas
    saliendo, que es lo unico que puede significar fisicamente.

    Los destinos que NO son origen en flujos directos son plantas que reciben
    directo del area, y ahi no hay nada que propagar: la fila del area ya es la
    que ve la planta.
    """
    if fdi is None or fdi.empty or COL_AREA not in fdi.columns:
        return fdi

    origenes = {_clave(a) for a in fdi[COL_AREA].dropna()}
    if not origenes:
        return fdi

    def entrante(tabla):
        if tabla is None or tabla.empty:
            return {}
        agrupado = (tabla.assign(_k=tabla[COL_DESTINO].map(_clave))
                    .groupby("_k")[COL_VOLUMEN].sum())
        return {k: float(v) for k, v in agrupado.items()}

    antes, despues = entrante(yac_antes), entrante(yac_despues)

    ajustados = []

    for clave_destino in origenes:
        viejo = antes.get(clave_destino, 0.0)
        nuevo = despues.get(clave_destino, 0.0)

        if viejo <= _EPS or abs(nuevo - viejo) <= _EPS:
            continue

        factor = nuevo / viejo
        filas = _mascara_clave(fdi[COL_AREA], clave_destino)

        if not filas.any():
            continue

        fdi.loc[filas, COL_VOLUMEN] = fdi.loc[filas, COL_VOLUMEN] * factor
        ajustados.append((clave_destino, factor))

    if ajustados:
        detalle = ", ".join(f"{n} x{f:.3f}" for n, f in sorted(ajustados))
        informe.avisos.append(
            f"Se reajustó la entrega de {len(ajustados)} ducto(s) porque cambió "
            f"el gas que les entra: {detalle}. Sin esto el pool de las plantas "
            "contaría dos veces el volumen redirigido.")

    return fdi


def _pisar_cromato(fila, cromato, compuestos):
    """Escribe la cromatografia en las columnas de compuesto de una fila.

    Si no se cargo ninguna, se deja la de la fila plantilla: es la del area (o
    la de la ruta hacia esa planta), que es la suposicion razonable — el gas del
    ducto nuevo es el mismo gas del area.
    """
    if cromato is None:
        return

    for compuesto in compuestos:
        if compuesto in fila.index and compuesto in cromato.index:
            fila[compuesto] = float(cromato[compuesto])


def _plantilla_flujo_directo(fdi, planta, compuestos):
    """Fila plantilla para el tramo ducto -> planta.

    Se busca en tres pasos, de mejor a peor:

    1. Una fila que YA vaya a esa planta. Es la ideal: hereda el esquema y
       cualquier columna derivada con un valor plausible para esa ruta.
    2. Cualquier fila de la tabla. Sirve igual: lo unico que se necesita es el
       esquema de columnas, y los tres campos que importan se pisan despues.
       Hace falta porque una planta que recibe INYECCION DIRECTA de areas (MEGA,
       TBX El Porton) no tiene ninguna fila en flujos directos, y sin este paso
       no se le podia abrir un ducto.
    3. Si la tabla esta vacia, se arma la fila de cero con sus columnas.

    Devuelve None solo si la tabla no tiene ni columnas, que ya seria otro
    problema.
    """
    if fdi is None:
        return None

    hacia = fdi[_mascara_clave(fdi[COL_DESTINO], planta)]
    if not hacia.empty:
        return hacia.iloc[0].copy()

    if not fdi.empty:
        return fdi.iloc[0].copy()

    if len(fdi.columns):
        return pd.Series(
            {c: (0.0 if c in set(compuestos) | {COL_VOLUMEN} else None)
             for c in fdi.columns})

    return None


def _declarar_en_matriz(matriz, planta, nombre, informe):
    """Agrega el ducto nuevo a la columna de la planta en `matriz_inyecciones`.

    `io_plantas` usa `matriz[nombre_planta]` como la lista de origenes
    declarados para validar el pool. Si el ducto nuevo no figura ahi, en el
    mejor caso sale un aviso y en el peor la fila se descarta y el alta no hace
    nada. Declararlo es barato y evita las dos cosas.
    """
    if matriz is None:
        return matriz

    if planta not in matriz.columns:
        informe.avisos.append(
            f"'{planta}' no es una columna de `matriz_inyecciones`; no se pudo "
            f"declarar '{nombre}' como origen. Si el pool de la planta no lo "
            "toma, es por esto.")
        return matriz

    if _mascara_clave(matriz[planta], nombre).any():
        return matriz

    # En la matriz va la forma CRUDA: `io_plantas` le aplica `normalizar` antes
    # de mergear, asi que meterla ya normalizada tambien funcionaria, pero
    # cruda es lo consistente con el resto de la hoja.
    fila = {c: None for c in matriz.columns}
    fila[planta] = str(nombre)
    return pd.concat([matriz, pd.DataFrame([fila])], ignore_index=True)
