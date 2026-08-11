import pandas as pd
import numpy as np
from io_.data_io import flujos_directos, yacimientos, detalles_hubs, propiedades, plantas_yacimientos, matriz_inyecciones, coefs_inyeccion_area, premisas_areas
from domain.normalizacion import *
from domain.ctes_gas import COMPUESTOS

flujos_directos = flujos_directos.fillna(0)
yacimientos = yacimientos.fillna(0)
detalles_hubs = detalles_hubs.fillna(0)
propiedades = propiedades.fillna(0)

plantas_yacimientos['Area'] = plantas_yacimientos['Area'].apply(normalizar)

yacimientos['Area'] = yacimientos['Area'].apply(normalizar)


matriz_inyecciones = matriz_inyecciones.melt(
    var_name="Gasoducto",
    value_name="Area"
)

matriz_inyecciones['Area'] = matriz_inyecciones['Area'].apply(normalizar)
matriz_inyecciones.fillna('error')

coefs_inyeccion_area['Area'] = coefs_inyeccion_area['Area'].apply(normalizar)

coefs_inyeccion_area = coefs_inyeccion_area.melt(
    id_vars= ['Area', 'Gasoducto'],
    var_name = "Periodo",
    value_name = "Coef_Inyeccion"
)

coefs_inyeccion_area["Periodo"] = pd.to_datetime(coefs_inyeccion_area["Periodo"], format="%m-%Y")



premisas_areas['Area'] = premisas_areas['Area'].apply(normalizar)


propiedades = propiedades[propiedades["Compuesto"].isin(COMPUESTOS)]

propiedades = propiedades.set_index('Compuesto')

propiedades['PCS [kJ/mol]'] = propiedades['Peso molecular [kg/kmol]'] * propiedades['PCS [MJ/kg]']