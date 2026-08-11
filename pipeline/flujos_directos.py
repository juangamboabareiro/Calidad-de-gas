from domain.normalizacion import normalizar

def calcular_inyeccion_flujos_directos(flujos_directos, matriz_inyecciones):

    flujos_directos["Area"] = flujos_directos["Area"].apply(normalizar)

    inyeccion_flujos_directos = matriz_inyecciones.merge(
        flujos_directos.melt(
        id_vars=["Area", "Inyección"],
        var_name="Gasoducto",
        value_name="Volumen"
        ), 
        left_on="Area", 
        right_on="Gasoducto",
        how = 'right')

    inyeccion_flujos_directos['Area'] = inyeccion_flujos_directos['Area_y']

    inyeccion_flujos_directos = inyeccion_flujos_directos.drop('Area_y', axis = 1)

    inyeccion_flujos_directos = inyeccion_flujos_directos.drop('Area_x', axis = 1)

    inyeccion_flujos_directos['Gasoducto'] = inyeccion_flujos_directos['Gasoducto_y']

    inyeccion_flujos_directos = inyeccion_flujos_directos.drop('Gasoducto_x', axis = 1)

    inyeccion_flujos_directos = inyeccion_flujos_directos.drop('Gasoducto_y', axis = 1)

    inyeccion_flujos_directos = inyeccion_flujos_directos[inyeccion_flujos_directos['Volumen'] != 0]

    return inyeccion_flujos_directos