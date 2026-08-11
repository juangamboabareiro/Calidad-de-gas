def calcular_inyeccion(inyeccion_std, plantas_yacimientos):
    ####### CREO INYECCION COMO INY STD PERO GROUPEADO POR MEAN AÑO #######

    inyeccion = (inyeccion_std.groupby(["Anio", "Area", "Cuenca"])['Volumen'].mean().unstack("Anio"))

    inyeccion = inyeccion.merge(plantas_yacimientos, on = "Area", how="left")

    inyeccion['HUB'] = inyeccion['HUB'].fillna("Otros")

    return inyeccion




def calcular_inyeccion_area(inyeccion, matriz_inyecciones):

    inyeccion_area = inyeccion.merge(matriz_inyecciones, on="Area", how = 'left')

    return inyeccion_area
