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

    activa: bool = True

    def a_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "nombre": self.nombre,
            "area_origen": self.area_origen,
            "planta_destino": self.planta_destino,
            "volumen": float(self.volumen),
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

    # Las BAJAS primero: si un alta manda gas a un ducto que despues se da de
    # baja, el resultado depende del orden. Bajas antes deja el estado "ducto
    # fuera de servicio" y despues el alta se reparte sobre lo que quedo, que es
    # el orden en que uno lo pensaria.
    for intervencion in [i for i in activas if i.tipo == "baja"]:
        yac, fdi = _baja(yac, fdi, intervencion, informe)

    for intervencion in [i for i in activas if i.tipo == "alta"]:
        yac, fdi, matriz = _alta(yac, fdi, matriz, intervencion, compuestos, informe)

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
            f"'{nombre}': no hay ninguna fila de flujos directos hacia "
            f"'{planta}' para usar de plantilla. ¿El nombre de la planta es el "
            "mismo que usa la columna `Gasoducto`?")
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
    """Una fila existente que vaya a esa planta, para clonar el esquema."""
    hacia = fdi[_mascara_clave(fdi[COL_DESTINO], planta)]
    if hacia.empty:
        return None
    return hacia.iloc[0].copy()


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
