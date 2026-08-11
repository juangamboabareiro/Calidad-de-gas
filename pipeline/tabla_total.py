import pandas as pd
from domain.normalizacion import normalizar


def query_volumen_tabla_total(df1, df2, PERIODO_CONSIDERADO):

    df_query = df1.merge(
        df2.query("Periodo == @PERIODO_CONSIDERADO")[["Area", "Volumen"]],
        on="Area",
        how="left"
    )

    return df_query


def query_coef_inyeccion_tabla_total(df1, df2, PERIODO_CONSIDERADO):

    df_query = df1.merge(
        df2.query("Periodo == @PERIODO_CONSIDERADO")[["Area", "Gasoducto", "Coef_Inyeccion"]],
        on=["Area", "Gasoducto"],
        how="left"
    )

    return df_query


def calcular_tabla_total_yacimientos(inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area, premisas_areas, PERIODO_CONSIDERADO, COMPUESTOS):

    tabla_total_yacimientos = pd.DataFrame()
    tabla_total_yacimientos["Area"] =  inyeccion_yacimientos_areas["Area"]
    tabla_total_yacimientos["HUB"] =  inyeccion_yacimientos_areas["HUB"]
    tabla_total_yacimientos["Gasoducto"] =  inyeccion_yacimientos_areas["Gasoducto"]

    tabla_total_yacimientos = query_volumen_tabla_total(df1=tabla_total_yacimientos, df2=inyeccion_std, PERIODO_CONSIDERADO=PERIODO_CONSIDERADO)

    tabla_total_yacimientos = query_coef_inyeccion_tabla_total(df1=tabla_total_yacimientos, df2=coefs_inyeccion_area, PERIODO_CONSIDERADO=PERIODO_CONSIDERADO)

    tabla_total_yacimientos["Volumen_inyectado"] = tabla_total_yacimientos["Volumen"] * tabla_total_yacimientos["Coef_Inyeccion"]


    tabla_total_yacimientos = tabla_total_yacimientos.merge(
        premisas_areas,
        on="Area",
        how="left"
    )

    tabla_total_yacimientos = tabla_total_yacimientos.drop_duplicates(subset = ['Area', 'Gasoducto'])


    ### Esto es agregar los datos de croma y calcular el volumen por compuesto


    vol_compuestos = (
        tabla_total_yacimientos[COMPUESTOS]
        .mul(tabla_total_yacimientos['Volumen_inyectado'], axis=0)
        .add_prefix('Vol_')
    )

    tabla_total_yacimientos = pd.concat([tabla_total_yacimientos, vol_compuestos], axis=1)

    tabla_total_yacimientos = tabla_total_yacimientos.fillna(0)

    return tabla_total_yacimientos







def calcular_tabla_total_flujos_directos(inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas, PERIODO_CONSIDERADO,COMPUESTOS):

    tabla_total_flujos_directos = inyeccion_flujos_directos


    tabla_total_flujos_directos = query_coef_inyeccion_tabla_total(df1=tabla_total_flujos_directos, df2=coefs_inyeccion_area, PERIODO_CONSIDERADO=PERIODO_CONSIDERADO)


    tabla_total_flujos_directos["Volumen_inyectado"] = tabla_total_flujos_directos["Volumen"] * tabla_total_flujos_directos["Coef_Inyeccion"]



    tabla_total_flujos_directos = tabla_total_flujos_directos.merge(
        premisas_areas,
        on="Area",
        how="left"
    )

    tabla_total_flujos_directos = tabla_total_flujos_directos.drop_duplicates(subset = ['Area', 'Gasoducto'])


    vol_COMPUESTOS = (
        tabla_total_flujos_directos[COMPUESTOS]
        .mul(tabla_total_flujos_directos['Volumen_inyectado'], axis=0)
        .add_prefix('Vol_')
    )

    tabla_total_flujos_directos = pd.concat([tabla_total_flujos_directos, vol_COMPUESTOS], axis=1)

    tabla_total_flujos_directos = tabla_total_flujos_directos.fillna(0)


    return tabla_total_flujos_directos





def calcular_tabla_total_detalles_hubs(detalles_hubs_areas, premisas_areas):

    detalles_hubs_areas_aux =  detalles_hubs_areas.melt(
        id_vars=["Area", "Gasoducto", "HUB"],
        var_name="Destino",
        value_name="Volumen_inyectado")

    detalles_hubs_areas_aux = detalles_hubs_areas_aux[detalles_hubs_areas_aux['Volumen_inyectado'] != 0]

    detalles_hubs_areas_aux['Destino'] = detalles_hubs_areas_aux['Destino'].apply(normalizar)


    tabla_total_detalles_hubs = detalles_hubs_areas_aux.merge(
        premisas_areas,
        on="Area",
        how="inner"
    )

    return tabla_total_detalles_hubs
