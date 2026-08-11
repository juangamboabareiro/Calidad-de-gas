from domain.normalizacion import normalizar, asignar_estacion
import pandas as pd
import numpy as np



def calcular_inyeccion_std(inyeccion_9300, coeficientes):

    ###### NORMALIZO INY 9300 A STD CON COEFS ######
    inyeccion_std = pd.concat([inyeccion_9300.iloc[:, :2],  inyeccion_9300.iloc[:, 2:]/coeficientes.iloc[:, 1:]], axis = 1)

    inyeccion_std = inyeccion_std.replace([np.inf, -np.inf], 0).fillna(0)

    inyeccion_std['Area'] = inyeccion_std['Area'].apply(normalizar)


    ####### TRABAJO SOBRE SERIE TEMPORAL PARA MEAN POR AÑO ######

    inyeccion_std = inyeccion_std.melt(
        id_vars = ['Area', 'Cuenca'],
        var_name = "Periodo",
        value_name = "Volumen"
    )

    inyeccion_std["Periodo"] = pd.to_datetime(inyeccion_std["Periodo"], format="%m-%Y")

    inyeccion_std["Anio"] = inyeccion_std["Periodo"].dt.year

    inyeccion_std["Mes"] = inyeccion_std["Periodo"].dt.month

    inyeccion_std["Estacion"] = inyeccion_std["Mes"].apply(asignar_estacion)

    return inyeccion_std


