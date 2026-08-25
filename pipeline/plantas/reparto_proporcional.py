"""
Reparto del sobrante de una planta entre varios destinos, por proporcion.
=========================================================================

Este modulo NO decide cuanto gas trata una planta: de eso se encargan
`modelar_TTY` y `modelar_MEGA`, que son los modelos ya validados. Aca solo se
reparte el SOBRANTE (lo que la planta no pudo tratar) entre las plantas
conectadas.

Por que alcanza con post-repartir
---------------------------------
`vol_asignado` no depende de como se reparta el sobrante: sale de
`min(vol_disponible, vol_maximo)`, y `vol_maximo` sale de la capacidad de
evacuacion. Entonces se puede llamar al modelo original con el tope de
derivacion en infinito, tomar el `sobrante` que devuelve y repartirlo aca sin
tocar nada de la fisica. El resultado es exacto, no una aproximacion.

Sigue valiendo la identidad que cierra el balance del eslabon:

    vol_disponible == vol_asignado + sum(derivados) + bypass

TOPES Y REDISTRIBUCION
----------------------
Una rama puede tener `tope` (limite fisico del traspaso). Si el reparto
proporcional le asigna mas de lo que aguanta, el excedente NO cae directo a
bypass: se reofrece a las ramas que todavia tienen lugar, en proporcion entre
ellas. Es lo que haria un splitter real, y evita que agregar una rama con tope
chico empeore el resultado global.

DOS BYPASS DISTINTOS
--------------------
- ESTRUCTURAL: si las proporciones suman menos de 1, esa fraccion del sobrante
  nunca se ofrece a nadie. El usuario dijo "de lo que sobre, manda el 30% a tal
  planta"; el otro 70% es bypass por definicion y NO se redistribuye.
- POR TOPE: una rama que se satura libera volumen que SI se reofrece.

Si las proporciones suman mas de 1 se renormaliza hacia abajo: no se puede
derivar mas sobrante del que hay.
"""

_MAX_PASADAS = 12
_EPS = 1e-9

INFINITO = float("inf")


def repartir_entre_destinos(monto, conexiones=None, ignorar_topes=False):
    """Reparte `monto` entre las conexiones. Devuelve (derivados, sin_asignar).

    Parameters
    ----------
    monto : float
        Volumen a repartir (el `sobrante` de la planta).
    conexiones : list[dict] | None
        Cada una: {'destino': str, 'proporcion': float, 'tope': float}.
        Se aceptan dicts para no acoplar este modulo al registro.
    ignorar_topes : bool
        Para una planta fuera de servicio: el tope de traspaso no aplica porque
        el tren no existe, todo el gas pasa de largo.
    """

    conexiones = list(conexiones or [])
    monto = max(float(monto), 0.0)

    if not conexiones:
        return {}, monto

    derivados = {c["destino"]: 0.0 for c in conexiones}
    capacidad = {
        c["destino"]: (INFINITO if ignorar_topes else float(c.get("tope", INFINITO)))
        for c in conexiones
    }
    peso = {c["destino"]: max(float(c.get("proporcion", 0.0)), 0.0) for c in conexiones}

    total_peso = sum(peso.values())
    reservado = monto * min(total_peso, 1.0)
    bypass_estructural = monto - reservado

    restante = reservado

    for _ in range(_MAX_PASADAS):
        if restante <= _EPS:
            break

        # Ramas con lugar Y con peso. Una rama en 0% no recibe nada aunque
        # sobre gas: el usuario la puso en 0 a proposito.
        libres = [d for d in derivados
                  if peso[d] > 0 and capacidad[d] - derivados[d] > _EPS]
        if not libres:
            break

        peso_libre = sum(peso[d] for d in libres)
        a_repartir = restante
        movido = 0.0

        for d in libres:
            objetivo = a_repartir * peso[d] / peso_libre
            entrega = min(objetivo, capacidad[d] - derivados[d])
            derivados[d] += entrega
            movido += entrega

        restante -= movido

        if movido <= _EPS:
            break

    return derivados, max(restante, 0.0) + bypass_estructural


def repartir_flujo_proporcional(vol_disponible, vol_maximo, conexiones=None,
                                activa=True, ignorar_topes=None):
    """Version completa (llenar + repartir), para usar sin los modelos de planta.

    En la cascada NO se usa: ahi el llenado lo hace `modelar_TTY`/`modelar_MEGA`
    y solo se post-reparte con `repartir_entre_destinos`. Queda para tests y
    para cualquier calculo suelto.
    """

    if ignorar_topes is None:
        ignorar_topes = not activa

    vol_disponible = max(float(vol_disponible), 0.0)
    vol_maximo = float(vol_maximo)

    vol_asignado = min(vol_disponible, vol_maximo) if activa else 0.0
    sobrante = vol_disponible - vol_asignado

    derivados, bypass = repartir_entre_destinos(sobrante, conexiones, ignorar_topes)

    return {
        "vol_disponible": vol_disponible,
        "vol_maximo": vol_maximo,
        "vol_asignado": vol_asignado,
        "sobrante": sobrante,
        "vol_derivado": sum(derivados.values()),
        "derivados": derivados,
        "bypass": bypass,
        "ocupacion": (
            vol_asignado / vol_maximo
            if vol_maximo not in (0, INFINITO) else None),
        "activa": activa,
    }
