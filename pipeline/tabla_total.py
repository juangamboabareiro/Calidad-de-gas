import pandas as pd

from domain.normalizacion import normalizar
from pipeline.cromatografia import agregar_cromatografia


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


def calcular_tabla_total_yacimientos(inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area, premisas_por_ruta, premisas_por_clave, sufijos_planta, PERIODO_CONSIDERADO, COMPUESTOS):

    tabla_total_yacimientos = pd.DataFrame()
    tabla_total_yacimientos["Area"] =  inyeccion_yacimientos_areas["Area"]
    tabla_total_yacimientos["HUB"] =  inyeccion_yacimientos_areas["HUB"]
    tabla_total_yacimientos["Gasoducto"] =  inyeccion_yacimientos_areas["Gasoducto"]

    tabla_total_yacimientos = query_volumen_tabla_total(df1=tabla_total_yacimientos, df2=inyeccion_std, PERIODO_CONSIDERADO=PERIODO_CONSIDERADO)

    tabla_total_yacimientos = query_coef_inyeccion_tabla_total(df1=tabla_total_yacimientos, df2=coefs_inyeccion_area, PERIODO_CONSIDERADO=PERIODO_CONSIDERADO)

    tabla_total_yacimientos["Volumen_inyectado"] = tabla_total_yacimientos["Volumen"] * tabla_total_yacimientos["Coef_Inyeccion"]


    ### Esto es agregar los datos de croma y calcular el volumen por compuesto

    # Antes: merge(premisas_areas, on="Area") + drop_duplicates(['Area','Gasoducto']).
    # Eso agarraba las dos filas de Fortin de Piedra (Planta y Otra) y se quedaba
    # con la primera segun el orden de la hoja. Ahora la clave es Area+Sufijo,
    # con el sufijo saliendo del par (Area, Gasoducto). El drop_duplicates se va:
    # con la clave correcta el merge ya es 1:1, y dejarlo taparia un problema.
    tabla_total_yacimientos = agregar_cromatografia(
        tabla_total_yacimientos,
        premisas_por_ruta,
        premisas_por_clave,
        sufijos_planta,
        COMPUESTOS,
        nombre="tabla_total_yacimientos",
    )

    # Las filas sin cromatografia vienen en NaN a proposito. Se rellenan aca, a
    # la vista: si el reporte de arriba dice "N filas SIN cromatografia", este
    # fillna las convierte en gas vacio y hay que ir a mirar por que.
    tabla_total_yacimientos[COMPUESTOS] = tabla_total_yacimientos[COMPUESTOS].fillna(0)


    vol_compuestos = (
        tabla_total_yacimientos[COMPUESTOS]
        .mul(tabla_total_yacimientos['Volumen_inyectado'], axis=0)
        .add_prefix('Vol_')
    )

    tabla_total_yacimientos = pd.concat([tabla_total_yacimientos, vol_compuestos], axis=1)

    tabla_total_yacimientos = tabla_total_yacimientos.fillna(0)

    return tabla_total_yacimientos







def calcular_tabla_total_flujos_directos(inyeccion_flujos_directos, coefs_inyeccion_area, premisas_por_ruta, premisas_por_clave, sufijos_planta, PERIODO_CONSIDERADO, COMPUESTOS):

    tabla_total_flujos_directos = inyeccion_flujos_directos


    tabla_total_flujos_directos = query_coef_inyeccion_tabla_total(df1=tabla_total_flujos_directos, df2=coefs_inyeccion_area, PERIODO_CONSIDERADO=PERIODO_CONSIDERADO)


    tabla_total_flujos_directos["Volumen_inyectado"] = tabla_total_flujos_directos["Volumen"] * tabla_total_flujos_directos["Coef_Inyeccion"]


    # Las premisas de gasoducto estan repetidas una vez por destino con valores
    # identicos (Pampa SCH x3, YPF - RDM x2...). Mergeando por Area salia un
    # producto cartesiano de 3x3 que el drop_duplicates volvia a bajar a 3.
    # `preparar_premisas` las colapsa de entrada, y revienta si alguna vez
    # difieren.
    tabla_total_flujos_directos = agregar_cromatografia(
        tabla_total_flujos_directos,
        premisas_por_ruta,
        premisas_por_clave,
        sufijos_planta,
        COMPUESTOS,
        nombre="tabla_total_flujos_directos",
    )

    tabla_total_flujos_directos[COMPUESTOS] = tabla_total_flujos_directos[COMPUESTOS].fillna(0)


    vol_COMPUESTOS = (
        tabla_total_flujos_directos[COMPUESTOS]
        .mul(tabla_total_flujos_directos['Volumen_inyectado'], axis=0)
        .add_prefix('Vol_')
    )

    tabla_total_flujos_directos = pd.concat([tabla_total_flujos_directos, vol_COMPUESTOS], axis=1)

    tabla_total_flujos_directos = tabla_total_flujos_directos.fillna(0)


    return tabla_total_flujos_directos





def calcular_tabla_total_detalles_hubs(detalles_hubs_areas, premisas_por_ruta, premisas_por_clave, sufijos_planta, COMPUESTOS):

    detalles_hubs_areas_aux =  detalles_hubs_areas.melt(
        id_vars=["Area", "Gasoducto", "HUB"],
        var_name="Destino",
        value_name="Volumen_inyectado")

    detalles_hubs_areas_aux = detalles_hubs_areas_aux[detalles_hubs_areas_aux['Volumen_inyectado'] != 0]

    detalles_hubs_areas_aux['Destino'] = detalles_hubs_areas_aux['Destino'].apply(normalizar)


    # El sufijo se busca por (Area, Gasoducto), igual que en el Excel: Gasoducto
    # es el ducto al que inyecta el area, Destino es la columna melteada.
    #
    # Aca ademas se arregla un bug que no tenian las otras dos: esta funcion no
    # hacia drop_duplicates, asi que Fortin de Piedra quedaba DUPLICADO (una fila
    # por cada premisa) y su volumen se contaba dos veces al agregar.
    tabla_total_detalles_hubs = agregar_cromatografia(
        detalles_hubs_areas_aux,
        premisas_por_ruta,
        premisas_por_clave,
        sufijos_planta,
        COMPUESTOS,
        nombre="tabla_total_detalles_hubs",
    )

    # Esta tabla mezcla renglones de area con renglones que son HUBs o plantas
    # ("hubsierrabarrosa", "tbxelporton"), que no tienen cromatografia. El merge
    # original era how="inner" y por eso los descartaba. Se mantiene ese
    # comportamiento, pero explicito: sin cromato, la fila se va.
    sin_croma = tabla_total_detalles_hubs[COMPUESTOS].isna().all(axis=1)
    tabla_total_detalles_hubs = tabla_total_detalles_hubs[~sin_croma]

    return tabla_total_detalles_hubs
