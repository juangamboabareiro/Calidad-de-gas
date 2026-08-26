"""
Ruteo por HUB: area -> HUB -> planta.

POR QUE EXISTE
--------------
El armado original conectaba cada area directo a su planta destino (MEGA, TTY,
TBX El Porton): toda fila de tabla_total_yacimientos con Gasoducto == planta
entraba al pool de la planta con su cromatografia individual. Pero fisicamente
solo inyectan directo las areas SIN hub. Las que comparten un HUB mandan su gas
primero al hub, que lo mezcla y lo deriva a las plantas.

La diferencia numerica aparece cuando un hub reparte entre varios destinos:
cada planta debe recibir la croma DEL HUB (premisa cargada o mezcla), no la de
cada area por separado, y el reparto es una decision del hub, no de cada area.
Si un hub manda el 100% a una sola planta y su croma es la mezcla volumetrica,
gas_rico_IN no cambia (la mezcla es lineal): ese es el caso de control para
validar la migracion.

QUE HACE
--------
`calcular_ruteo_hubs` toma tabla_total_yacimientos y:

1. Separa las rutas area -> planta en directas (HUB == HUB_DEFAULT) y via hub.
2. Por hub, junta el volumen que le llega y decide su croma de salida:
   - hoja Cromas-HUBs si el hub esta cargado (input explicito);
   - si no, mezcla volumetrica de las areas que aportan, con aviso.
3. Deriva ese volumen a las plantas segun el reparto de Detalles-HUBs (los
   renglones cuyo Area ES un hub). El reparto se usa como PROPORCION sobre las
   columnas de planta, no como volumen absoluto: el balance cierra en cada
   periodo aunque la hoja tenga volumenes cargados en otro momento.
4. Devuelve:
   - tabla_total_yacimientos SIN las rutas que se rutearon (para que
     armar_input_planta no las cuente doble),
   - tabla_total_hubs con filas (Area=hub, Gasoducto=planta, croma, Vol_*),
     lista para entrar a armar_input_planta como tercera fuente,
   - un informe con el mapa area->hub y los avisos.

REGLA DE SEGURIDAD
------------------
Un hub sin renglon de reparto utilizable en Detalles-HUBs deja a sus areas
inyectando directo, como antes, con aviso. Perder volumen en silencio es peor
que mantener el comportamiento viejo para ese hub puntual.

Las rutas area -> gasoducto (VMN, VMS, Pampa SCH...) NO se tocan: el hub solo
intermedia la entrega a plantas; lo que viaja por ducto ya tiene su propia
croma de ruta en las premisas.
"""

from __future__ import annotations

import pandas as pd

from domain.columnas import COL_AREA, COL_GASODUCTO, COL_HUB, HUB_DEFAULT
from domain.normalizacion import normalizar

COL_VOL_INY = "Volumen_inyectado"

# Con volumenes menores a esto (en unidad de Volumen_inyectado) no se puede
# calcular una mezcla confiable: la division amplifica ruido y signos.
_EPS_VOLUMEN = 1e-9


# ===========================================================================
# Claves de hub
# ===========================================================================

def _claves_hub(etiqueta: str) -> set[str]:
    """Variantes normalizadas con las que un hub puede figurar como Area.

    En Plantas-Yacimientos el hub se llama, p. ej., "Sierra Barrosa" o
    "Hub Centro (resto)", pero en Detalles-HUBs el renglon del hub aparece como
    "hubsierrabarrosa". Se prueba la etiqueta tal cual, con prefijo "hub" y
    sin el prefijo si ya lo trae.
    """
    base = normalizar(etiqueta)
    claves = {base, normalizar(f"hub {etiqueta}")}
    if base.startswith("hub"):
        claves.add(base[3:])
    return {c for c in claves if c}


def _match_croma_hub(cromas_hubs, etiqueta_hub, compuestos):
    """Busca la croma del hub en la hoja Cromas-HUBs. None si no esta.

    La hoja acepta la clave en una columna HUB o Area, indistinto.
    """
    if cromas_hubs is None or not len(cromas_hubs):
        return None

    col_clave = next(
        (c for c in (COL_HUB, COL_AREA) if c in cromas_hubs.columns), None)
    if col_clave is None:
        print("[ruteo_hubs] Cromas-HUBs no tiene columna HUB ni Area: se ignora la hoja")
        return None

    claves = _claves_hub(etiqueta_hub)
    filas = cromas_hubs[cromas_hubs[col_clave].map(normalizar).isin(claves)]

    if not len(filas):
        return None

    if len(filas) > 1:
        print(f"[ruteo_hubs] '{etiqueta_hub}' cargado {len(filas)} veces en "
              "Cromas-HUBs, se toma la primera")

    croma = filas.iloc[0].reindex(compuestos)
    croma = pd.to_numeric(croma, errors="coerce").fillna(0.0)

    suma = float(croma.sum())
    if suma < 0.5:
        # Fila a medio llenar (o vacia) en la hoja: tomarla seria inyectar un
        # gas de ceros. Se ignora y el hub cae a la mezcla volumetrica.
        print(f"[ruteo_hubs] croma de '{etiqueta_hub}' en Cromas-HUBs suma "
              f"{suma:.4f} (fila incompleta): se ignora y se usa la mezcla")
        return None
    if not (0.98 <= suma <= 1.02):
        print(f"[ruteo_hubs] OJO croma de '{etiqueta_hub}' en Cromas-HUBs suma "
              f"{suma:.4f} (deberia ser ~1)")

    return croma


def _mezcla_volumetrica(filas, compuestos):
    """Croma del hub como promedio ponderado por volumen de sus areas.

    Es la misma cuenta que hace io_plantas para el pool: fracciones por
    Volumen_relativo. Los volumenes negativos (retiros) pesan con su signo,
    igual que en el pool.
    """
    total = float(filas[COL_VOL_INY].sum())
    if abs(total) < _EPS_VOLUMEN:
        return None
    pesos = filas[COL_VOL_INY] / total
    return filas[compuestos].fillna(0.0).T.dot(pesos)


# ===========================================================================
# Reparto hub -> plantas desde Detalles-HUBs
# ===========================================================================

def _repartos_de_hubs(detalles_hubs_areas, hubs, plantas):
    """Fracciones de reparto hub -> planta, leidas de los renglones-hub.

    Parameters
    ----------
    detalles_hubs_areas : pandas.DataFrame
        Hoja Detalles-HUBs preprocesada (ancha: Area, Gasoducto, HUB + una
        columna por destino).
    hubs : iterable[str]
        Etiquetas de hub (valores de la columna HUB) que hay que rutear.
    plantas : iterable[str]
        Destinos que cuentan como planta.

    Returns
    -------
    dict[str, pandas.Series]
        etiqueta_hub -> Serie indexada por planta (nombre tal como esta en
        `plantas`) con la fraccion de reparto. Solo hubs con reparto usable.
    """
    if detalles_hubs_areas is None or not len(detalles_hubs_areas):
        return {}

    id_vars = [c for c in (COL_AREA, COL_GASODUCTO, COL_HUB)
               if c in detalles_hubs_areas.columns]
    columnas_destino = [c for c in detalles_hubs_areas.columns if c not in id_vars]

    clave_planta = {normalizar(p): p for p in plantas}
    area_norm = detalles_hubs_areas[COL_AREA].map(normalizar)

    repartos: dict[str, pd.Series] = {}

    for hub in hubs:
        filas = detalles_hubs_areas[area_norm.isin(_claves_hub(hub))]
        if not len(filas):
            continue

        # Un hub puede tener mas de un renglon (p. ej. por ducto de salida):
        # se suman, porque el reparto que importa es el agregado del hub.
        volumenes = (
            filas[columnas_destino]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0.0)
            .sum(axis=0)
        )

        # Solo las columnas que son plantas: lo que el hub manda a ductos no
        # se rutea aca (ese gas ya viaja por yacimientos/flujos_directos).
        por_planta = {}
        for destino, vol in volumenes.items():
            clave = normalizar(destino)
            if clave in clave_planta and vol != 0:
                por_planta[clave_planta[clave]] = (
                    por_planta.get(clave_planta[clave], 0.0) + float(vol))

        total = sum(por_planta.values())
        if total <= _EPS_VOLUMEN:
            print(f"[ruteo_hubs] '{hub}' figura en Detalles-HUBs pero sin volumen "
                  "hacia plantas: sus areas quedan inyectando directo")
            continue

        repartos[hub] = pd.Series(por_planta) / total

    return repartos


# ===========================================================================
# Ruteo principal
# ===========================================================================

def calcular_ruteo_hubs(
    tabla_total_yacimientos: pd.DataFrame,
    detalles_hubs_areas: pd.DataFrame,
    compuestos,
    plantas,
    cromas_hubs: pd.DataFrame | None = None,
):
    """
    Reencamina las rutas area -> planta de las areas con hub via su HUB.

    Parameters
    ----------
    tabla_total_yacimientos : pandas.DataFrame
        Tabla total de inyeccion primaria, YA con cromatografia y columna HUB.
    detalles_hubs_areas : pandas.DataFrame
        Hoja Detalles-HUBs preprocesada (ancha). De aca salen los renglones
        de reparto de cada hub.
    compuestos : list[str]
    plantas : iterable[str]
        Nombres de los destinos que son plantas (p. ej. ("TTY", "MEGA",
        "TBX El Porton")). Solo esas rutas se rutean via hub.
    cromas_hubs : pandas.DataFrame | None
        Hoja Cromas-HUBs (columna HUB o Area + compuestos). Opcional: si un
        hub no figura, su croma es la mezcla volumetrica de sus areas.

    Returns
    -------
    yacimientos_ajustada : pandas.DataFrame
        tabla_total_yacimientos sin las filas que se rutearon via hub.
    tabla_total_hubs : pandas.DataFrame
        Una fila por (hub, planta): Area=clave del hub, HUB=etiqueta,
        Gasoducto=planta, Volumen_inyectado, croma y Vol_ por compuesto.
    informe : dict
        - "mapa_area_hub": {area normalizada -> clave de hub} SOLO de las
          areas efectivamente ruteadas (para traducir la validacion contra
          la matriz de inyecciones en armar_input_planta).
        - "hubs_ruteados", "hubs_sin_reparto", "hubs_con_croma_cargada",
          "hubs_con_mezcla": listas de etiquetas.
        - "volumen_ruteado": float.
    """
    tabla = tabla_total_yacimientos
    informe = {
        "mapa_area_hub": {},
        "hubs_ruteados": [],
        "hubs_sin_reparto": [],
        "hubs_con_croma_cargada": [],
        "hubs_con_mezcla": [],
        "volumen_ruteado": 0.0,
    }

    columnas_hub_out = ([COL_AREA, COL_HUB, COL_GASODUCTO, COL_VOL_INY]
                        + list(compuestos)
                        + [f"Vol_{c}" for c in compuestos])
    tabla_total_hubs = pd.DataFrame(columns=columnas_hub_out)

    if tabla is None or not len(tabla) or COL_HUB not in tabla.columns:
        print("[ruteo_hubs] sin tabla de yacimientos o sin columna HUB: no se rutea nada")
        return tabla, tabla_total_hubs, informe

    claves_plantas = {normalizar(p) for p in plantas}
    a_planta = tabla[COL_GASODUCTO].astype(str).map(normalizar).isin(claves_plantas)
    con_hub = tabla[COL_HUB].fillna(HUB_DEFAULT).ne(HUB_DEFAULT)

    candidatas = tabla[a_planta & con_hub]
    if not len(candidatas):
        print("[ruteo_hubs] ninguna ruta area->planta tiene HUB asignado: no se rutea nada")
        return tabla, tabla_total_hubs, informe

    hubs = sorted(candidatas[COL_HUB].unique())
    repartos = _repartos_de_hubs(detalles_hubs_areas, hubs, plantas)

    filas_hub: list[dict] = []
    indices_ruteados: list = []

    for hub in hubs:
        filas_areas = candidatas[candidatas[COL_HUB] == hub]

        reparto = repartos.get(hub)

        if reparto is None:
            # Sin renglon en Detalles-HUBs. Si TODAS las rutas del hub van a
            # una sola planta, el reparto es 100% por construccion y no hace
            # falta hoja: se rutea igual (lo que importa ahi es la croma del
            # hub, no la fraccion). Con mas de un destino si se necesita el
            # renglon, porque el reparto es una decision del hub que este
            # modulo no puede inventar.
            destinos = filas_areas[COL_GASODUCTO].map(normalizar).unique()
            if len(destinos) == 1:
                destino_original = filas_areas[COL_GASODUCTO].iloc[0]
                reparto = pd.Series({destino_original: 1.0})
                print(f"[ruteo_hubs] '{hub}' sin renglon en Detalles-HUBs pero "
                      f"con un solo destino ({destino_original}): reparto "
                      "trivial 100%")
            else:
                informe["hubs_sin_reparto"].append(hub)
                print(f"[ruteo_hubs] '{hub}' sin reparto en Detalles-HUBs y con "
                      f"{len(destinos)} destinos: sus {len(filas_areas)} rutas "
                      "area->planta quedan directas (como antes)")
                continue

        volumen_hub = float(filas_areas[COL_VOL_INY].sum())

        croma = _match_croma_hub(cromas_hubs, hub, compuestos)
        if croma is not None:
            informe["hubs_con_croma_cargada"].append(hub)
        else:
            croma = _mezcla_volumetrica(filas_areas, compuestos)
            if croma is None:
                informe["hubs_sin_reparto"].append(hub)
                print(f"[ruteo_hubs] '{hub}' sin croma cargada y con volumen ~0 "
                      "(no se puede mezclar): sus rutas quedan directas")
                continue
            informe["hubs_con_mezcla"].append(hub)
            print(f"[ruteo_hubs] '{hub}' sin croma en Cromas-HUBs: se usa la "
                  "mezcla volumetrica de sus areas")

        clave_hub = normalizar(f"hub {hub}") if not normalizar(hub).startswith("hub") \
            else normalizar(hub)

        for planta, fraccion in reparto.items():
            fila = {
                COL_AREA: clave_hub,
                COL_HUB: hub,
                COL_GASODUCTO: planta,
                COL_VOL_INY: volumen_hub * float(fraccion),
            }
            for c in compuestos:
                valor = float(croma.get(c, 0.0))
                fila[c] = valor
                fila[f"Vol_{c}"] = valor * fila[COL_VOL_INY]
            filas_hub.append(fila)

        indices_ruteados.extend(filas_areas.index.tolist())
        informe["hubs_ruteados"].append(hub)
        informe["volumen_ruteado"] += volumen_hub

        for area in filas_areas[COL_AREA].map(normalizar).unique():
            informe["mapa_area_hub"][area] = clave_hub

        destinos = ", ".join(f"{p} {f:.0%}" for p, f in reparto.items())
        print(f"[ruteo_hubs] '{hub}': {len(filas_areas)} areas, "
              f"{volumen_hub:,.0f} de volumen -> {destinos}")

    yacimientos_ajustada = tabla.drop(index=indices_ruteados)

    if filas_hub:
        tabla_total_hubs = pd.DataFrame(filas_hub).reindex(columns=columnas_hub_out)

    # Chequeo de balance: lo que salio de yacimientos tiene que ser exactamente
    # lo que entro a la tabla de hubs. Si no, el ruteo creo o destruyo gas.
    desvio = abs(informe["volumen_ruteado"]
                 - float(tabla_total_hubs[COL_VOL_INY].sum() if len(tabla_total_hubs) else 0.0))
    if desvio > 1e-6:
        print(f"[ruteo_hubs] OJO desvio de balance en el ruteo: {desvio:,.6f}")

    print(f"[ruteo_hubs] ruteados {len(informe['hubs_ruteados'])} hubs, "
          f"{informe['volumen_ruteado']:,.0f} de volumen; "
          f"{len(informe['hubs_sin_reparto'])} sin reparto quedaron directos")

    return yacimientos_ajustada, tabla_total_hubs, informe
