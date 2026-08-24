# region MODULOS

import pandas as pd
import numpy as np
from domain.ctes_gas import *
from pipeline.preprocesamiento import preprocesar_inputs
import config
from pipeline.inyeccion_std import calcular_inyeccion_std
from io_.loaders import load_inyeccion_9300, load_coeficientes, load_retenidos_rtp
from pipeline.inyeccion_area import calcular_inyeccion_area, calcular_inyeccion
from pipeline.yacimientos import calcular_inyeccion_yacimientos_areas
from pipeline.detalles_hubs import calcular_detalles_hubs_areas
from pipeline.flujos_directos import calcular_inyeccion_flujos_directos
from pipeline.tabla_total import calcular_tabla_total_yacimientos, calcular_tabla_total_flujos_directos, calcular_tabla_total_detalles_hubs
from domain.propiedades_gas import calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.MEGA import modelar_MEGA
from pipeline.plantas.TTY import modelar_TTY
from outputs.writers import guardar
from io_.loaders import load_flujos_directos, load_yacimientos, load_detalles_hubs, load_propiedades, load_plantas_yacimientos, load_matriz_inyecciones, load_premisas_areas, load_coefs_inyeccion_area, load_retenidos_rtp
from pipeline.plantas.flujo_plantas import calcular_DERIVACION
from pipeline.cromatografia import (
    cargar_sufijos_planta,
    validar_sufijos,
    preparar_premisas,
)
from pipeline.cromatografia import (
    cargar_sufijos_planta,
    validar_sufijos,
    preparar_premisas,
)
from pipeline.preprocesamiento import validar_destinos_matriz

from scripts.preparar_geo import compactar, leer_geojson, argparse


# endregion



# region inputs

inyeccion_9300 = load_inyeccion_9300(config.PATH_INPUTS)
coeficientes = load_coeficientes(config.PATH_INPUTS)
retenidos_RTP = load_retenidos_rtp(config.PATH_INPUTS)

flujos_directos = load_flujos_directos(config.PATH_INPUTS)
yacimientos = load_yacimientos(config.PATH_INPUTS)
detalles_hubs = load_detalles_hubs(config.PATH_INPUTS)
propiedades = load_propiedades(config.PATH_INPUTS)
plantas_yacimientos = load_plantas_yacimientos(config.PATH_INPUTS)

#endregion


# region preprocesamiento de datos

inputs = preprocesar_inputs(
    flujos_directos=flujos_directos,
    yacimientos=yacimientos,
    detalles_hubs=detalles_hubs,
    propiedades=propiedades,
    plantas_yacimientos=plantas_yacimientos,
)

flujos_directos      = inputs["flujos_directos"]
yacimientos          = inputs["yacimientos"]
detalles_hubs        = inputs["detalles_hubs"]
propiedades          = inputs["propiedades"]
plantas_yacimientos  = inputs["plantas_yacimientos"]
matriz_inyecciones   = inputs["matriz_inyecciones"]
coefs_inyeccion_area = inputs["coefs_inyeccion_area"]
premisas_areas       = inputs["premisas_areas"]


sufijos_planta = cargar_sufijos_planta(config.PATH_INPUTS)
premisas_por_ruta, premisas_por_clave = preparar_premisas(premisas_areas, COMPUESTOS, sufijos_planta)


sufijos_planta = cargar_sufijos_planta(config.PATH_INPUTS)
premisas_por_ruta, premisas_por_clave = preparar_premisas(
    premisas_areas, COMPUESTOS, sufijos_planta)
# endregion



# region tablas totales

inyeccion_std = calcular_inyeccion_std(inyeccion_9300, coeficientes)
inyeccion = calcular_inyeccion(inyeccion_std, plantas_yacimientos)
inyeccion_area = calcular_inyeccion_area(inyeccion, matriz_inyecciones)

yacimientos_areas, inyeccion_yacimientos_areas = calcular_inyeccion_yacimientos_areas(
    yacimientos=yacimientos,
    plantas_yacimientos=plantas_yacimientos,
    inyeccion_area=inyeccion_area,
)

detalles_hubs_areas = calcular_detalles_hubs_areas(detalles_hubs, plantas_yacimientos)

inyeccion_flujos_directos = calcular_inyeccion_flujos_directos(flujos_directos)


tabla_total_yacimientos = calcular_tabla_total_yacimientos(
    inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area,
    premisas_por_ruta, premisas_por_clave, sufijos_planta,
    config.PERIODO_CONSIDERADO, COMPUESTOS)

tabla_total_flujos_directos = calcular_tabla_total_flujos_directos(
    inyeccion_flujos_directos, coefs_inyeccion_area,
    premisas_por_ruta, premisas_por_clave, sufijos_planta,
    config.PERIODO_CONSIDERADO, COMPUESTOS)

tabla_total_detalles_hubs = calcular_tabla_total_detalles_hubs(
    detalles_hubs_areas,
    premisas_por_ruta, premisas_por_clave, sufijos_planta, COMPUESTOS)



tabla_total_yacimientos = calcular_propiedades_gas(tabla_total_yacimientos, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)
tabla_total_flujos_directos = calcular_propiedades_gas(tabla_total_flujos_directos, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)
tabla_total_detalles_hubs = calcular_propiedades_gas(tabla_total_detalles_hubs, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)



guardar(tabla_total_yacimientos, 'TBL_TTL_YCS.csv')
guardar(tabla_total_flujos_directos, 'TBL_TTL_DTOS.csv')
guardar(tabla_total_detalles_hubs, 'TBL_TTL_DH.csv')

# endregion



validar_sufijos(sufijos_planta, premisas_areas,
                [inyeccion_yacimientos_areas, inyeccion_flujos_directos])


# region modelado de plantas — CASCADA

retenidos_TTY_DP = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'Dew point']
retenidos_TTY_TBX = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'TBX']
retenidos_MEGA = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'TBX MEGA']


# TTY-DP y TTY-TBX son dos trenes sobre el MISMO pool de gas (la misma columna
# de matriz_inyecciones), no dos plantas en paralelo. El pool se reparte en
# cascada, y "llenarse" siempre significa agotar la capacidad de EVACUACION DE
# LGN (el ingreso de gas rara vez limita, entra solo como min() adicional).
#
#   pre-PM  :  pool TTY --------------> DP --(sobra)--> MEGA --(sobra)--> bypass
#              (TBX no esta en servicio)  \--(resto)--> bypass DP
#
#   post-PM :  pool TTY --> TBX --(sobra)--> DP --(sobra)--> MEGA --(sobra)--> bypass
#                            \--(resto)--> bypass TBX
#
# El traspaso TBX -> DP NO usa calcular_DERIVACION: los dos trenes comparten el
# pool, entonces la cromato es identica y basta pasarle el volumen sobrante via
# vol_disponible. La unica derivacion real es DP -> MEGA, donde el gas entra a un
# pool de otra composicion y hay que sumarlo a la mezcla.


TBX_EN_SERVICIO = config.PERIODO_CONSIDERADO >= config.FECHA_PM_TTY_TBX


comunes = dict(
    matriz_inyecciones=matriz_inyecciones,
    calcular_retenidos=calcular_retenidos,
    tabla_total_flujos_directos=tabla_total_flujos_directos,
    tabla_total_yacimientos=tabla_total_yacimientos,   # nuevo
    propiedades=propiedades,
    COMPUESTOS=COMPUESTOS,
)


# 1) TTY-TBX: primer eslabon. Toma todo el pool y se llena; el sobrante pasa a DP
#    hasta MAX_DERIVACION_TTY_TBX_A_TTY_DP, y lo que exceda ese tope es bypass.
#    Pre-PM esta fuera de servicio (activa=False): no toma nada y deja pasar todo.
TTY_TBX = modelar_TTY(**comunes,
                      retenidos_TTY=retenidos_TTY_TBX,
                      CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_TBX,
                      CAPACIDAD_TTY=config.CAPACIDAD_TTY_TBX,
                      MAX_DERIVACION_PLANTA_A_PLANTA=(
                          config.MAX_DERIVACION_TTY_TBX_A_TTY_DP if TBX_EN_SERVICIO else float('inf')),
                      activa=TBX_EN_SERVICIO)


# 2) TTY-DP: recibe el sobrante de TBX. Pre-PM eso es el pool completo, porque
#    TBX no toma nada y su tope de traspaso es infinito (el gas va directo a DP).
TTY_DP = modelar_TTY(**comunes,
                     retenidos_TTY=retenidos_TTY_DP,
                     CAPACIDAD_EVACUACION_TTY=config.CAPACIDAD_EVACUACION_TTY_DP,
                     CAPACIDAD_TTY=config.CAPACIDAD_TTY_DP,
                     vol_disponible=TTY_TBX['flujos']['vol_derivado'],
                     MAX_DERIVACION_PLANTA_A_PLANTA=config.MAX_DERIVACION_TTY_DP_A_MEGA)


# 3) TTY-DP -> MEGA: aca si es derivacion, el gas entra a un pool de otra
#    composicion y tiene que pesar en la mezcla de MEGA.
derivacion_TTY_DP_a_MEGA = calcular_DERIVACION(
    flujos_origen=TTY_DP['flujos'],
    gas_rico_IN_origen=TTY_DP['gas_rico_IN'],
    nombre_origen='tty_dp')


MEGA = modelar_MEGA(**comunes,
                    retenidos_MEGA=retenidos_MEGA,
                    CAPACIDAD_EVACUACION_MEGA=config.CAPACIDAD_EVACUACION_MEGA,
                    CAPACIDAD_MEGA=config.CAPACIDAD_MEGA,
                    derivaciones=[derivacion_TTY_DP_a_MEGA])


tabla_tty_tbx = TTY_TBX['tabla_total']
tabla_tty_dp = TTY_DP['tabla_total']
tabla_mega = MEGA['tabla_total']


columnas_flujos = ['vol_disponible', 'vol_maximo', 'vol_asignado', 'sobrante',
                   'vol_derivado', 'bypass', 'lgn_unitario', 'lgn_asignado', 'activa']

flujos_plantas = pd.DataFrame({
    'TTY_TBX': TTY_TBX['flujos'],
    'TTY_DP': TTY_DP['flujos'],
    'MEGA': MEGA['flujos'],
}).T.reindex(columns=columnas_flujos)


# Balance por eslabon: vol_disponible == vol_asignado + vol_derivado + bypass.
# El vol_derivado de un eslabon es el vol_disponible del siguiente, entonces la
# cadena cierra sin doble conteo. OJO: sum(tabla_mega) incluye la fila de
# derivacion de DP, asi que no se puede sumar con sum(tabla_tty_dp).
_desvio = (
    flujos_plantas['vol_disponible']
    - flujos_plantas[['vol_asignado', 'vol_derivado', 'bypass']].sum(axis=1)
).abs().max()

assert _desvio < 1e-6, f'El balance por eslabon no cierra: {_desvio}'

print(f"TBX en servicio: {TBX_EN_SERVICIO}")
print(flujos_plantas)

# endregion



red_gasoductos = pd.DataFrame(columns=["origen", "destino", "valor"])

red_gasoductos[["origen", "destino", "valor"]] = tabla_total_yacimientos[['Area', 'Gasoducto', 'Volumen_inyectado']]


iny = inyeccion_9300.set_index(["Area", "Cuenca"])
coef = coeficientes.set_index("Area")
coef_al = coef.reindex(iny.index.get_level_values("Area"))
coef_al.index = iny.index

print("celdas totales:      ", iny.size)
print("vacías en el origen: ", iny.isna().sum().sum())
print("coef = 0:            ", ((coef_al == 0) & iny.notna()).sum().sum())
print("coef ausente:        ", (coef_al.isna() & iny.notna()).sum().sum())
print("áreas equivalentes:  ", 3494 / (iny.shape[1]))


perdido = iny[(coef_al == 0) & iny.notna() & (iny != 0)]
print(perdido.sum().sum())


gasoductos = {g for g in inyeccion_area["Gasoducto"].dropna() if isinstance(g, str)}
print(sorted(gasoductos - set(yacimientos.columns)))
print(sorted(c for c in yacimientos.columns if isinstance(c, str)))


import geopandas as gpd
g = gpd.read_file("datos/crudo/concesiones-hidrocarburos.shp")
print(g.crs)
print(list(g.columns))
print(g.head(3).drop(columns="geometry").to_string())


import geopandas as gpd
d = gpd.read_file("datos/crudo/ductos-hidrocarburos.shp")
print(len(d), list(d.columns))
for c in d.columns:
    if d[c].dtype == object and d[c].nunique() < 40:
        print(c, "→", d[c].value_counts().head(12).to_dict())



d = gpd.read_file("datos/crudo/ductos-hidrocarburos.shp", ignore_geometry=True)
for c in ["TIPO", "TIPO_TRAMO", "MATERIAL"]:
    print(c, "→", d[c].value_counts().head(15).to_dict(), "\n")
print(d["DIAMETRO"].describe())