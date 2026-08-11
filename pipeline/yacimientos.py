def calcular_inyeccion_yacimientos_areas(yacimientos, plantas_yacimientos, inyeccion_area):

    yacimientos_areas = yacimientos.merge(plantas_yacimientos, on = "Area", how="left")

    yacimientos_areas['HUB'] = yacimientos_areas['HUB'].fillna("Otros")


    inyeccion_yacimientos_areas = inyeccion_area.merge(
     yacimientos_areas.melt(
        id_vars=["Area", "Inyección"],
        var_name="Gasoducto",
        value_name="Volumen"
        ),
        left_on=["Area", "Gasoducto"],
        right_on=["Area", "Gasoducto"],
        how="left"   
    )


    inyeccion_yacimientos_areas['Inyección'] = inyeccion_yacimientos_areas['Inyección'].fillna('Primaria')

    inyeccion_yacimientos_areas['Volumen'] = inyeccion_yacimientos_areas['Volumen'].fillna(0)

    return inyeccion_yacimientos_areas