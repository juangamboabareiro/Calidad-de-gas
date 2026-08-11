from domain.normalizacion import normalizar

def calcular_inyeccion_detalles_hubs(detalles_hubs, plantas_yacimientos):

    detalles_hubs["Area"] = detalles_hubs["Area"].apply(normalizar)

    detalles_hubs_areas = detalles_hubs.merge(plantas_yacimientos, on = "Area", how="left")

    detalles_hubs_areas['HUB'] = detalles_hubs_areas['HUB'].fillna("Otros")

    inyeccion_detalles_hubs_areas = detalles_hubs_areas

    return inyeccion_detalles_hubs_areas