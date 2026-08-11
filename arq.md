```
proyecto/
├── config.py                  # paths, capacidad, periodo_considerado, fecha_random
├── io_/
│   └── loaders.py             # funciones (no module-level!) para leer cada sheet,
│                               # con path configurable. Ej: load_constantes_gas(path)
├── domain/
│   ├── normalizacion.py       # normalizar, asignar_estacion
│   ├── propiedades_gas.py     # calcular_propiedades_gas
│   └── ctes_gas.py           # calcular_retenidos, calcular_energia_total, correcciones
├── pipeline/
│   ├── preprocesamiento.py    # fillna + normalizar sobre inputs crudos
│   ├── inyeccion.py           # inyeccion_std, inyeccion, inyeccion_area
│   ├── yacimientos.py         # yacimientos_areas, inyeccion_yacimientos_areas
│   ├── detalles_hubs.py
│   ├── flujos_directos.py
│   ├── tabla_total.py         # tabla_total_yacimientos + tabla_total_flujos_directos
│   │                           # (comparten ~90% de lógica, se puede parametrizar)
│   └── plantas/
        |-- MEGA.py
        |-- planta_template.py
        |-- TBX_EP.py
        |-- TTY_DP.py
        |-- TTY_TBX.py
        |-- VM_LIQ.py 
├── outputs/
│   └── writers.py             # to_csv centralizado, opcional (flag para activar/desactivar)
└── main.py
|-- config.py
|-- app.py                    # orquesta: carga → preprocesa → pipeline → outputs
```


#domain

##ctes_gas:

```
from io_.loaders import load_constantes_gas
from config import PATH_INPUTS

constantes_GAS = load_constantes_gas(PATH_INPUTS)

DENSIDAD_AIRE = 1.225
CONVERSION_BARRILLES_KGD = 6.29 #Pasaje de KG/D a BBL/D


PRESION_BASE = constantes_GAS['Presion Base [kPa]'].values[0]
CONSTANTE_GAS = constantes_GAS['Cte. GAS [m3.kPa/(K.kmol)]'].values[0]
TEMPERATURA_BASE = constantes_GAS["Temperatura Base [°C]"].values[0]
CONVERSION = constantes_GAS['Conversion'].values[0]
MMBtu = 252074

METANO = ['C1']
ETANO = ['C2']
PROPANO = ['C3']
BUTANOS = ['iC4', 'nC4']
GASOLINA = ['iC5', 'nC5', 'nC6', 'nC7', 'nC8', 'nC9', 'nC10']
COMPUESTOS = METANO + ETANO + PROPANO + BUTANOS + GASOLINA + ['N2', 'CO2']
```





##normalizacion

```
import unicodedata
import pandas as pd
import numpy as np


def normalizar(texto):
    """
    Normaliza una cadena de texto para facilitar comparaciones.

    La función:
    - Convierte el texto a minúsculas.
    - Elimina espacios al inicio y al final.
    - Elimina tildes y otros signos diacríticos.
    - Conserva únicamente caracteres alfanuméricos.

    Parameters
    ----------
    texto : str | any
        Valor a normalizar. Si es nulo (NaN), se devuelve sin modificar.

    Returns
    -------
    str | any
        Texto normalizado o el valor original si es NaN.

    Examples
    --------
    normalizar("Gas Rico")
    'gasrico'

    normalizar("Área_1")
    'area1'

    normalizar("  Cañadón Seco  ")
    'canadonseco'
    """

    if pd.isna(texto):
        return texto

    texto = str(texto).strip().lower()

    # sacar tildes
    texto = ''.join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )

    # dejar solo letras y números
    texto = ''.join(c for c in texto if c.isalnum())

    return texto


def asignar_estacion(mes):
    """
    Clasifica un mes como verano o invierno según el criterio operativo.

    Parameters
    ----------
    mes : int
        Número de mes (1-12).

    Returns
    -------
    str
        'verano' para los meses 1, 2, 3, 4, 11 y 12.
        'invierno' para los meses 5, 6, 7, 8 y 9.
        'error' si el valor no corresponde a un mes válido.
    """

    if mes in [1, 2, 3, 4, 11, 12]:
        return "verano"

    if mes in [5, 6, 7, 8, 9]:
        return "invierno"

    return "error"


```



##propiedades_gas


```
import pandas as pd
import numpy as np


def calcular_propiedades_gas(
        df: pd.DataFrame, 
        df2: pd.DataFrame, 
        lista_de_cols, 
        PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS,DENSIDAD_AIRE,CONVERSION) -> pd.DataFrame:
    """
    Calcula las df2 físicas del gas
    a partir de su composición molar.

    Las df2 calculadas son:

    - Factor de compresibilidad (z)
    - Densidad relativa al aire
    - Poder Calorífico Superior (PCS)
    - Índice de Wobbe (IW)

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame que contiene las composiciones molares de las corrientes.

    lista_de_cols : list[str]
        Lista de columnas correspondientes a los componentes de la mezcla
        gaseosa (C1, C2, C3, CO2, N2, etc.).

    Returns
    -------
    None
        La función agrega y actualiza las columnas 'z', 'densidad',
        'PCS' e 'IW' directamente en el DataFrame recibido.

    Notes
    -----
    Los cálculos utilizan las df2 individuales de cada componente
    almacenadas en la tabla 'df2' y las constantes operativas
    definidas en 'constantes_GAS'.
    """
    res = df
    
    z = 1 - (PRESION_BASE) * ((df[lista_de_cols].dot(df2['Factor b']))**2)

    res['z'] = z

    densidad = df[lista_de_cols].dot(df2['Peso molecular [kg/kmol]'])

    densidad = PRESION_BASE * densidad / z / (273.15 + TEMPERATURA_BASE) / CONSTANTE_GAS / DENSIDAD_AIRE

    res['densidad'] = densidad


    PCS = df[lista_de_cols].dot(df2['PCS [kJ/mol]'])

    PCS = PRESION_BASE * PCS / z / (273.15 + TEMPERATURA_BASE) / CONSTANTE_GAS / CONVERSION * 1000

    res['PCS'] = PCS


    IW = (PCS/np.sqrt(densidad)).fillna(0)

    res['IW'] = IW

    


    return res



def calcular_retenidos(propiedades, volumen_total, retenido, gas_rico_in, PRESION_BASE, CONSTANTE_GAS, TEMPERATURA_BASE):

    num = PRESION_BASE * volumen_total * propiedades['Peso molecular [kg/kmol]'] * gas_rico_in * retenido
    denom = propiedades['Z'] * CONSTANTE_GAS * (273.15 + TEMPERATURA_BASE)

    return (num/denom)



def calcular_energia_total(propiedades, volumen_total, gas_rico_in, PRESION_BASE, CONSTANTE_GAS, TEMPERATURA_BASE, MMBtu):

    num = PRESION_BASE * volumen_total * propiedades['Peso molecular [kg/kmol]'] * gas_rico_in
    denom = propiedades['Z'] * CONSTANTE_GAS * (273.15 + TEMPERATURA_BASE) * propiedades['PCS [kJ/mol]'] / MMBtu

    return (num/denom)
```



#io_

##loaders

```
import pandas as pd
###  Ver INPUTS en Data_Dictionary.md



def load_mapa(path):
    return pd.read_excel(path, sheet_name="Mapa", index_col="Num")

def load_coeficientes(path):
    return pd.read_excel(path, sheet_name="Coeficientes")

def load_inyeccion_9300(path):
    return pd.read_excel(path, sheet_name="Inyeccion-9300")

def load_premisas_areas(path):
    return pd.read_excel(path, sheet_name="Premisas-Areas")

def load_propiedades(path):
    return pd.read_excel(path, sheet_name="Propiedades")

def load_constantes_gas(path):
    return pd.read_excel(path, sheet_name="Constantes-GAS")

def load_matriz_inyecciones(path):
    return pd.read_excel(path, sheet_name="Matriz-Inyecciones", index_col="Num")

def load_flujos_directos(path):
    return pd.read_excel(path, sheet_name="Flujos-Directos")

def load_yacimientos(path):
    return pd.read_excel(path, sheet_name="Yacimientos")

def load_detalles_hubs(path):
    return pd.read_excel(path, sheet_name="Detalles-HUBs")

def load_coefs_inyeccion_area(path):
    return pd.read_excel(path, sheet_name="Coefs-Iny-Areas")

def load_plantas_yacimientos(path):
    return pd.read_excel(path, sheet_name="Plantas-Yacimientos")

def load_retenidos_rtp(path):
    return pd.read_excel(path, sheet_name="Retenidos-RTP")
```



#outputs

##writers

```
from pathlib import Path
import config

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"

def guardar(df, nombre, activar=config.GUARDAR_CSVS):
    if activar:
        ruta = OUTPUTS_DIR / f"{nombre}.csv"
        df.to_csv(ruta, index=False)

```


#pipeline

##detalles_hubs

```
from domain.normalizacion import normalizar

def calcular_inyeccion_detalles_hubs(detalles_hubs, plantas_yacimientos):

    detalles_hubs["Area"] = detalles_hubs["Area"].apply(normalizar)

    detalles_hubs_areas = detalles_hubs.merge(plantas_yacimientos, on = "Area", how="left")

    detalles_hubs_areas['HUB'] = detalles_hubs_areas['HUB'].fillna("Otros")

    inyeccion_detalles_hubs_areas = detalles_hubs_areas

    return inyeccion_detalles_hubs_areas
```



##flujos_directos

```
from domain.normalizacion import normalizar

def calcular_inyeccion_detalles_hubs(detalles_hubs, plantas_yacimientos):

    detalles_hubs["Area"] = detalles_hubs["Area"].apply(normalizar)

    detalles_hubs_areas = detalles_hubs.merge(plantas_yacimientos, on = "Area", how="left")

    detalles_hubs_areas['HUB'] = detalles_hubs_areas['HUB'].fillna("Otros")

    inyeccion_detalles_hubs_areas = detalles_hubs_areas

    return inyeccion_detalles_hubs_areas
```



##inyeccion_area
```
def calcular_inyeccion(inyeccion_std, plantas_yacimientos):
    ####### CREO INYECCION COMO INY STD PERO GROUPEADO POR MEAN AÑO #######

    inyeccion = (inyeccion_std.groupby(["Anio", "Area", "Cuenca"])['Volumen'].mean().unstack("Anio"))

    inyeccion = inyeccion.merge(plantas_yacimientos, on = "Area", how="left")

    inyeccion['HUB'] = inyeccion['HUB'].fillna("Otros")

    return inyeccion




def calcular_inyeccion_area(inyeccion, matriz_inyecciones):

    inyeccion_area = inyeccion.merge(matriz_inyecciones, on="Area", how = 'left')

    return inyeccion_area

```



##inyeccion_std
```
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



```


##preprocesamiento

```
import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones, load_coefs_inyeccion_area, load_premisas_areas
from config import PATH_INPUTS
from domain.normalizacion import *
from domain.ctes_gas import COMPUESTOS





def preprocesar_inputs(flujos_directos, yacimientos, detalles_hubs, propiedades, plantas_yacimientos):

    matriz_inyecciones = load_matriz_inyecciones(PATH_INPUTS)
    coefs_inyeccion_area = load_coefs_inyeccion_area(PATH_INPUTS)
    premisas_areas = load_premisas_areas(PATH_INPUTS)

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

    return flujos_directos, yacimientos, detalles_hubs, propiedades, plantas_yacimientos, matriz_inyecciones, coefs_inyeccion_area, premisas_areas
```



##tabla_total
```
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

```



##yacimientos

```
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
```


##plantas

###MEGA

```
 # import pandas as pd
import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones, load_retenidos_rtp
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS, CONVERSION_BARRILLES_KGD
from config import CAPACIDAD, FECHA_RANDOM, PERIODO_CONSIDERADO, CAPACIDAD_TTY_TBX, CAPACIDAD_ADICIONAL_TBX, CAPACIDAD_BASE_CONVERTIBLE_TBX, CAPACIDAD_MEGA, PATH_INPUTS
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


matriz_inyecciones = load_matriz_inyecciones(PATH_INPUTS)
retenidos_rtp = load_retenidos_rtp(PATH_INPUTS)


def modelar_MEGA(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_MEGA):

    tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=retenidos_MEGA,)


    if retenidos_vol.values.sum()/1000 > CAPACIDAD_MEGA:

        coef_correccion = CAPACIDAD_MEGA/(retenidos_vol.values.sum()/1000)


        retenidos_vol = retenidos_vol * coef_correccion

        return tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol

    else:

        return tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol
```


###planta_template

```
import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS
from config import CAPACIDAD, FECHA_RANDOM, PERIODO_CONSIDERADO, PATH_INPUTS
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos


matriz_inyecciones = load_matriz_inyecciones(PATH_INPUTS)


def io_plantas(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_planta):

    tabla_plantas = pd.DataFrame()
    tabla_plantas['Area'] = matriz_inyecciones['TTY']
    tabla_plantas['Area'] = tabla_plantas['Area'].apply(normalizar)


    tabla_plantas = tabla_plantas.merge(
        tabla_total_flujos_directos,
        on='Area',
        how='inner'
    )


    tabla_plantas['Volumen_relativo'] = tabla_plantas['Volumen_inyectado']/(tabla_plantas['Volumen_inyectado'].sum())

    tabla_plantas = tabla_plantas.fillna(0)

    gas_rico_IN = tabla_plantas[COMPUESTOS].T.dot(tabla_plantas['Volumen_relativo'])





    gas_residual_OUT = gas_rico_IN * (1 - retenidos_planta)

    retenidos = calcular_retenidos(propiedades, tabla_plantas['Volumen_inyectado'].sum(), retenidos_planta, gas_rico_IN, PRESION_BASE, CONSTANTE_GAS, TEMPERATURA_BASE).T



    etano_retenido = retenidos.loc[ETANO].sum()
    propano_retenido = retenidos.loc[PROPANO].sum()
    butanos_retenido = retenidos.loc[BUTANOS].sum()
    gasolina_retenido = retenidos.loc[GASOLINA].sum()


    retenidos_vol = pd.DataFrame({
        'etano' : etano_retenido,
        'propano' : propano_retenido,
        'butanos' : butanos_retenido,
        'gasolina' : gasolina_retenido
    })


    return tabla_plantas, gas_rico_IN, gas_residual_OUT, retenidos, retenidos_vol

```



###TTY_DP

```
import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones, load_retenidos_rtp
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS
from config import CAPACIDAD, FECHA_RANDOM, PERIODO_CONSIDERADO, PATH_INPUTS
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


matriz_inyecciones = load_matriz_inyecciones(PATH_INPUTS)
retenidos_rtp = load_retenidos_rtp(PATH_INPUTS)


# tbx = tabla_tty_tbx['Volumen inyectado']/tabla_tty_tbx['Volumen inyectado'].sum() * FLUJO_SIN_BYPASS_TBX 

# dp = max(min(tabla_tty_dp['Volumen inyectado']/tabla_tty_dp['Volumen inyectado'].sum() * CAPACIDAD, tabla_tty_tbx['Volumen inyectado'] - tbx), 0)

# FLUJO_SIN_BYPASS = min(tabla_tty_dp['Volumen inyectado'].sum(), dp.sum())

# BYPASS = min(tabla_tty_dp['Volumen inyectado'].sum() - )


def correccion_TTY_DP(retenidos_vol, PERIODO_CONSIDERADO, FECHA_RANDOM, CAPACIDAD, tabla_tty_dp, propiedades, gas_rico_IN, retenidos):

    etano_retenido = retenidos_vol['etano']

    propano_retenido = retenidos_vol['propano']

    butanos_retenido = retenidos_vol['butanos']

    gasolina_retenido = retenidos_vol['gasolina']

    correccion_gasolina = gasolina_retenido

    AUX  = (0 if PERIODO_CONSIDERADO < FECHA_RANDOM else 200)

    correccion_butanos = AUX if butanos_retenido.values > AUX else butanos_retenido

    correccion_propano = min(max(AUX - butanos_retenido.values, 0), propano_retenido.values)

    correccion_etano = etano_retenido


    coef_corr_propano = propano_retenido * 1000/(PRESION_BASE * min(CAPACIDAD, tabla_tty_dp['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[PROPANO] * gas_rico_IN.loc[PROPANO] * propiedades['Z'].loc[PROPANO] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE))

    coef_corr_butanos = (1000*retenidos.loc[BUTANOS]/butanos_retenido*correccion_butanos).values/(PRESION_BASE * min(CAPACIDAD, tabla_tty_dp['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[BUTANOS] * gas_rico_IN.fillna(0).loc[BUTANOS] * propiedades['Z'].loc[BUTANOS] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE)).values

    correcciones = pd.DataFrame({
        'etano' : correccion_etano,
        'propano' : correccion_propano,
        'butanos' : correccion_butanos,
        'gasolina' : correccion_gasolina
    })


    return correcciones, coef_corr_butanos, coef_corr_propano




def modelar_TTY_DP(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_TTY_DP):

    tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=retenidos_TTY_DP,)


    if tabla_tty_dp['Volumen_inyectado'].sum() > CAPACIDAD:

        correcciones, coef_corr_butanos, coef_corr_propano = correccion_TTY_DP(tabla_tty_dp=tabla_tty_dp, retenidos_vol=retenidos_vol, PERIODO_CONSIDERADO=PERIODO_CONSIDERADO, FECHA_RANDOM=FECHA_RANDOM, propiedades=propiedades, CAPACIDAD=CAPACIDAD, gas_rico_IN=gas_rico_IN, retenidos=retenidos)

        new_retenidos = retenidos_TTY_DP.T

        new_retenidos.loc[PROPANO] = coef_corr_propano.loc[PROPANO].fillna(0)



        for i in range(len(BUTANOS)):
            new_retenidos.loc[BUTANOS[i]] = np.ravel(coef_corr_butanos)[i]
    

        tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=new_retenidos.T)

        


    return tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol








```





###TTY_TBX

```
# import pandas as pd
import pandas as pd
import numpy as np
from io_.loaders import load_matriz_inyecciones, load_retenidos_rtp
from domain.normalizacion import normalizar
from domain.ctes_gas import PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, BUTANOS, PROPANO, GASOLINA, ETANO, COMPUESTOS, CONVERSION_BARRILLES_KGD
from config import CAPACIDAD, FECHA_RANDOM, PERIODO_CONSIDERADO, CAPACIDAD_TTY_TBX, CAPACIDAD_ADICIONAL_TBX, CAPACIDAD_BASE_CONVERTIBLE_TBX, PATH_INPUTS
from domain.propiedades_gas import calcular_energia_total, calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.planta_template import io_plantas


matriz_inyecciones = load_matriz_inyecciones(PATH_INPUTS)
retenidos_rtp = load_retenidos_rtp(PATH_INPUTS)


# tbx = tabla_tty_tbx['Volumen inyectado']/tabla_tty_tbx['Volumen inyectado'].sum() * FLUJO_SIN_BYPASS_TBX 

# dp = max(min(tabla_tty_tbx['Volumen inyectado']/tabla_tty_tbx['Volumen inyectado'].sum() * CAPACIDAD, tabla_tty_tbx['Volumen inyectado'] - tbx), 0)

# FLUJO_SIN_BYPASS = min(tabla_tty_tbx['Volumen inyectado'].sum(), dp.sum())

# BYPASS = min(tabla_tty_tbx['Volumen inyectado'].sum() - )






def correccion_TTY_TBX(retenidos_vol, PERIODO_CONSIDERADO, FECHA_RANDOM, CAPACIDAD, tabla_tty_tbx, propiedades, gas_rico_IN, retenidos):


    retenidos_vol = retenidos_vol/propiedades['Densidad Liquido [kg/m3]']

    etano_retenido = retenidos_vol['etano'] * CONVERSION_BARRILLES_KGD

    propano_retenido = retenidos_vol['propano'] * CONVERSION_BARRILLES_KGD

    butanos_retenido = retenidos_vol['butanos'] * CONVERSION_BARRILLES_KGD

    gasolina_retenido = retenidos_vol['gasolina'] * CONVERSION_BARRILLES_KGD

    correccion_gasolina = gasolina_retenido

    correccion_etano = etano_retenido


    AUX  = 90000 * CAPACIDAD_TTY_TBX / (CAPACIDAD_BASE_CONVERTIBLE_TBX + CAPACIDAD_ADICIONAL_TBX)


    ############################

    #BUTANOS CREO HAY ALGO RARO PORQUE NO SE SI ESTOY USANDO LA SUMA O LOS VALUES EN VERDAD

    ############################


    correccion_butanos = butanos_retenido.values if retenidos_vol.values.sum() <= AUX else butanos_retenido.values * AUX/retenidos_vol.values.sum()

    correccion_propano = AUX - correccion_etano - correccion_butanos.values - correccion_gasolina if propano_retenido.values > 1 else 0

    

    coef_corr_propano = propano_retenido * 1000/(PRESION_BASE * min(CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[PROPANO] * gas_rico_IN.loc[PROPANO] * propiedades['Z'].loc[PROPANO] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE))

    coef_corr_butanos = (1000*retenidos.loc[BUTANOS]/butanos_retenido*correccion_butanos).values/(PRESION_BASE * min(CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'].sum()) * propiedades['Peso molecular [kg/kmol]'].loc[BUTANOS] * gas_rico_IN.fillna(0).loc[BUTANOS] * propiedades['Z'].loc[BUTANOS] * CONSTANTE_GAS *(273.15 + TEMPERATURA_BASE)).values

    correcciones = pd.DataFrame({
        'etano' : correccion_etano,
        'propano' : correccion_propano,
        'butanos' : correccion_butanos,
        'gasolina' : correccion_gasolina
    })


    return correcciones, coef_corr_butanos, coef_corr_propano




def modelar_TTY_TBX(calcular_retenidos, tabla_total_flujos_directos, propiedades, COMPUESTOS, retenidos_TTY_TBX):

    tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=retenidos_TTY_TBX,)

    if tabla_tty_tbx['Volumen_inyectado'].sum() > CAPACIDAD:

        correcciones, coef_corr_butanos, coef_corr_propano = correccion_TTY_TBX(tabla_tty_tbx=tabla_tty_tbx, retenidos_vol=retenidos_vol, PERIODO_CONSIDERADO=PERIODO_CONSIDERADO, FECHA_RANDOM=FECHA_RANDOM, propiedades=propiedades, CAPACIDAD=CAPACIDAD, gas_rico_IN=gas_rico_IN, retenidos=retenidos)

        new_retenidos = retenidos_TTY_TBX.T

        new_retenidos.loc[PROPANO] = coef_corr_propano.loc[PROPANO].fillna(0)



        for i in range(len(BUTANOS)):
            new_retenidos.loc[BUTANOS[i]] = np.ravel(coef_corr_butanos)[i]
    

        tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = io_plantas(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_planta=new_retenidos.T)

        


    return tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol








```



#main
```
# region MODULOS

import pandas as pd
import numpy as np

from domain.ctes_gas import *
from pipeline.preprocesamiento import preprocesar_inputs
from config import CAPACIDAD, PERIODO_CONSIDERADO, FECHA_RANDOM, PATH_INPUTS, CAPACIDAD_MEGA
from pipeline.inyeccion_std import calcular_inyeccion_std
from io_.loaders import load_inyeccion_9300, load_coeficientes, load_retenidos_rtp
from pipeline.inyeccion_area import calcular_inyeccion_area, calcular_inyeccion
from pipeline.yacimientos import calcular_inyeccion_yacimientos_areas
from pipeline.detalles_hubs import calcular_inyeccion_detalles_hubs
from pipeline.flujos_directos import calcular_inyeccion_flujos_directos
from pipeline.tabla_total import calcular_tabla_total_yacimientos, calcular_tabla_total_flujos_directos, calcular_tabla_total_detalles_hubs
from domain.propiedades_gas import calcular_propiedades_gas, calcular_retenidos
from pipeline.plantas.TTY_DP import modelar_TTY_DP
from pipeline.plantas.TTY_TBX import modelar_TTY_TBX
from pipeline.plantas.MEGA import modelar_MEGA
from outputs.writers import guardar
from io_.loaders import load_flujos_directos, load_yacimientos, load_detalles_hubs, load_propiedades, load_plantas_yacimientos, load_matriz_inyecciones, load_premisas_areas, load_coefs_inyeccion_area, load_retenidos_rtp




# endregion



# region inputs

inyeccion_9300 = load_inyeccion_9300(PATH_INPUTS)
coeficientes = load_coeficientes(PATH_INPUTS)
retenidos_RTP = load_retenidos_rtp(PATH_INPUTS)

flujos_directos = load_flujos_directos(PATH_INPUTS)
yacimientos = load_yacimientos(PATH_INPUTS)
detalles_hubs = load_detalles_hubs(PATH_INPUTS)
propiedades = load_propiedades(PATH_INPUTS)
plantas_yacimientos = load_plantas_yacimientos(PATH_INPUTS)


#endregion



# region preprocesamiento de datos

flujos_directos, yacimientos, detalles_hubs, propiedades, plantas_yacimientos, matriz_inyecciones, coefs_inyeccion_area, premisas_areas = preprocesar_inputs(flujos_directos=flujos_directos, yacimientos=yacimientos, detalles_hubs=detalles_hubs, propiedades=propiedades, plantas_yacimientos=plantas_yacimientos)

# endregion




inyeccion_std = calcular_inyeccion_std(inyeccion_9300, coeficientes)
inyeccion = calcular_inyeccion(inyeccion_std, plantas_yacimientos)
inyeccion_area = calcular_inyeccion_area(inyeccion, matriz_inyecciones)

inyeccion_yacimientos_areas = calcular_inyeccion_yacimientos_areas(yacimientos, plantas_yacimientos, inyeccion_area)
inyeccion_detalles_hubs = calcular_inyeccion_detalles_hubs(detalles_hubs, plantas_yacimientos)
inyeccion_flujos_directos = calcular_inyeccion_flujos_directos(flujos_directos, matriz_inyecciones)

tabla_total_yacimientos = calcular_tabla_total_yacimientos(inyeccion_yacimientos_areas, inyeccion_std, coefs_inyeccion_area, premisas_areas, PERIODO_CONSIDERADO, COMPUESTOS)
tabla_total_flujos_directos = calcular_tabla_total_flujos_directos(inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas, PERIODO_CONSIDERADO, COMPUESTOS)
tabla_total_detalles_hubs = calcular_tabla_total_detalles_hubs(inyeccion_detalles_hubs, premisas_areas)

tabla_total_yacimientos = calcular_propiedades_gas(tabla_total_yacimientos, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)
tabla_total_flujos_directos = calcular_propiedades_gas(tabla_total_flujos_directos, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)
tabla_total_detalles_hubs = calcular_propiedades_gas(tabla_total_detalles_hubs, propiedades, COMPUESTOS, PRESION_BASE, TEMPERATURA_BASE, CONSTANTE_GAS, DENSIDAD_AIRE, CONVERSION)



guardar(tabla_total_yacimientos, 'TBL_TTL_YCS.csv')
guardar(tabla_total_flujos_directos, 'TBL_TTL_DTOS.csv')
guardar(tabla_total_detalles_hubs, 'TBL_TTL_DH.csv')


retenidos_TTY_DP = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'Dew point']
retenidos_TTY_TBX = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'TBX']
retenidos_MEGA = retenidos_RTP[COMPUESTOS][retenidos_RTP['Planta'] == 'TBX MEGA']


tabla_tty_dp, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = modelar_TTY_DP(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY_DP=retenidos_TTY_DP)


tabla_tty_tbx, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = modelar_TTY_TBX(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_TTY_TBX=retenidos_TTY_TBX)


tabla_mega, gas_rico_IN, gas_residual_OUT,  retenidos, retenidos_vol = modelar_MEGA(calcular_retenidos=calcular_retenidos, tabla_total_flujos_directos=tabla_total_flujos_directos, propiedades=propiedades, COMPUESTOS=COMPUESTOS, retenidos_MEGA = retenidos_MEGA)



#volumen_dp = max(min(tabla_tty_dp['Volumen_inyectado']/tabla_tty_dp['Volumen_inyectado'].values.sum()*CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'] - (tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * CAPACIDAD)),0)


#volumen_bypass = min(volumen_dp, 5)

# tabla_tty_dp['Volumen_inyectado'] = tabla_tty_dp['Volumen_inyectado']/1000000
# tabla_tty_tbx['Volumen_inyectado'] = tabla_tty_tbx['Volumen_inyectado']/1000000

# print(tabla_tty_dp['Volumen_inyectado']/tabla_tty_dp['Volumen_inyectado'].values.sum()*CAPACIDAD)
# print(tabla_tty_tbx['Volumen_inyectado'] - (tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * CAPACIDAD))

# volumen_dp = np.maximum(np.minimum(tabla_tty_dp['Volumen_inyectado']/tabla_tty_dp['Volumen_inyectado'].values.sum()*CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'] - (tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * CAPACIDAD)), 0)

# volumen_tbx = tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * np.minimum(tabla_tty_tbx['Volumen_inyectado'],CAPACIDAD)

# volumen_tbx_mega = np.minimum(tabla_mega['Volumen_inyectado']/(tabla_mega['Volumen_inyectado'].values.sum()) * CAPACIDAD_MEGA, tabla_mega['Volumen_inyectado'])

# #volumen_tbx_mega_aj = volumen_tbx_mega*coef_correccion

# print(volumen_dp)

# print(volumen_tbx)

# print(volumen_tbx_mega)




# volumen_bypass_tty_dp = max(tabla_tty_dp['Volumen_inyectado'].values.sum() - min(volumen_dp, tabla_tty_dp['Volumen_inyectado'].values.sum()) - min(tabla_tty_tbx['Volumen_inyectado'].values.sum(), CAPACIDAD_TTY_TBX))
# volumen_bypass_mega = min(volumen_bypass_tty_dp,5)
# volumen_bypass_tty_tbx = 0


# bypass_tty_dp_molar = gas_rico_IN_tty_tbx if gas_rico_IN_tty_dp == 0 else gas_rico_IN_tty_dp

# #print(min(tabla_tty_dp['Volumen_inyectado']/tabla_tty_dp['Volumen_inyectado'].values.sum()*CAPACIDAD, tabla_tty_tbx['Volumen_inyectado'] - (tabla_tty_tbx['Volumen_inyectado']/tabla_tty_tbx['Volumen_inyectado'].values.sum() * CAPACIDAD)))
```


#config
```
import pandas as pd
from pathlib import Path


PATH_INPUTS =  "datos/inputs.xlsx" 
PERIODO_CONSIDERADO = pd.Timestamp('01-2025')
FECHA_RANDOM = pd.Timestamp('01-2025') 
CAPACIDAD = 10
CAPACIDAD_TTY_TBX = 0
CAPACIDAD_BASE_CONVERTIBLE_TBX = 13.2
CAPACIDAD_ADICIONAL_TBX = 20.8
CAPACIDAD_MEGA = 37
GUARDAR_CSVS = True
```



#app

```
"""
Interfaz Streamlit para el pipeline de Balance de Gas (post-modularización).

Estructura:
  - Sidebar: carga de inputs.xlsx (o usa el default de config.PATH_INPUTS) +
    parámetros editables (período, capacidad, guardar CSVs).
  - Botón "Ejecutar pipeline": corre la misma secuencia que main.py (Fase 6
    del roadmap) pero con los parámetros que puso el usuario, en memoria
    (session_state), sin depender de reiniciar el proceso.
  - Tabs: uno por DataFrame de salida, con vista + botón de descarga CSV.
  - Sección aparte para los valores de dominio que no son DataFrame
    (calcular_correcciones, alerta de capacidad).

Ajustá los imports y las firmas marcadas con "# TODO" para que coincidan
exactamente con cómo terminaron tus módulos reales.
"""

import io
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from io_ import loaders
from pipeline import (
    preprocesamiento,
    inyeccion_std,
    inyeccion_area,
    yacimientos,
    detalles_hubs,
    flujos_directos,
    tabla_total,
)
from pipeline.plantas.planta_template import io_plantas as modelado_plantas
from domain.propiedades_gas import calcular_retenidos as dom_retenidos

st.set_page_config(page_title="Balance de Gas", layout="wide")
st.title("Balance de Gas — Pipeline")

# ---------------------------------------------------------------------------
# Sidebar: inputs y parámetros
# ---------------------------------------------------------------------------
st.sidebar.header("Inputs")

uploaded = st.sidebar.file_uploader(
    "inputs.xlsx (opcional — si no subís nada, usa el default de config.py)",
    type=["xlsx"],
)

if uploaded is not None:
    tmp_dir = tempfile.mkdtemp()
    input_path = str(Path(tmp_dir) / uploaded.name)
    with open(input_path, "wb") as f:
        f.write(uploaded.getbuffer())
else:
    input_path = config.PATH_INPUTS

st.sidebar.caption(f"Usando: `{input_path}`")

st.sidebar.header("Parámetros")

periodo = st.sidebar.text_input(
    "Período considerado (MM-YYYY)",
    value=config.PERIODO_CONSIDERADO.strftime("%m-%Y"),
)
try:
    periodo_ts = pd.Timestamp(periodo.replace("/", "-"))
except Exception:
    st.sidebar.error("Formato de período inválido, uso el default de config.")
    periodo_ts = config.PERIODO_CONSIDERADO

capacidad = st.sidebar.number_input(
    "Capacidad", value=float(config.CAPACIDAD), step=1.0
)

guardar_csvs = st.sidebar.checkbox("Guardar CSVs en disco al ejecutar", value=False)

run = st.sidebar.button("▶️ Ejecutar pipeline", type="primary")

# ---------------------------------------------------------------------------
# Ejecución del pipeline
# ---------------------------------------------------------------------------


def ejecutar_pipeline(path: str, periodo: pd.Timestamp, capacidad: float) -> dict:
    """Corre la secuencia de main.py y devuelve todo en un dict.

    # TODO: ajustar el orden/argumentos exactos de cada llamada a como
    # terminaron definidas tus funciones reales de pipeline/ y domain/.
    """
    resultados = {}

    with st.status("Cargando inputs...", expanded=False) as status:
        constantes_gas = loaders.load_constantes_gas(path)
        mapa = loaders.load_mapa(path)
        coeficientes = loaders.load_coeficientes(path)
        inyeccion_9300 = loaders.load_inyeccion_9300(path)
        premisas_areas = loaders.load_premisas_areas(path)
        propiedades = loaders.load_propiedades(path)
        matriz_inyecciones = loaders.load_matriz_inyecciones(path)
        flujos_directos_raw = loaders.load_flujos_directos(path)
        yacimientos_raw = loaders.load_yacimientos(path)
        detalles_hubs_raw = loaders.load_detalles_hubs(path)
        coefs_inyeccion_area = loaders.load_coefs_inyeccion_area(path)
        plantas_yacimientos = loaders.load_plantas_yacimientos(path)
        retenidos_rtp = loaders.load_retenidos_rtp(path)
        status.update(label="Inputs cargados ✅")

    with st.status("Preprocesando...", expanded=False) as status:
        flujos_directos_df, yacimientos_df, detalles_hubs_df, propiedades, plantas_yacimientos, matriz_inyecciones, coefs_inyeccion_area, premisas_areas = (
            preprocesamiento.preprocesar_inputs(
                flujos_directos_raw, yacimientos_raw, detalles_hubs_raw,
                propiedades, plantas_yacimientos,
            )
        )
        status.update(label="Preprocesamiento listo ✅")

    with st.status("Corriendo inyección...", expanded=False) as status:
        inyeccion_std_df = inyeccion_std.calcular_inyeccion_std(inyeccion_9300, coeficientes)
        inyeccion_df, inyeccion_area_df = inyeccion_area.calcular_inyeccion_area(
            inyeccion_std_df, plantas_yacimientos, matriz_inyecciones
        )
        status.update(label="Inyección lista ✅")

    with st.status("Yacimientos y hubs...", expanded=False) as status:
        yacimientos_areas, inyeccion_yacimientos_areas = (
            yacimientos.calcular_yacimientos_areas(
                yacimientos_df, plantas_yacimientos, inyeccion_area_df
            )
        )
        detalles_hubs_areas = detalles_hubs.calcular_detalles_hubs_areas(
            detalles_hubs_df, plantas_yacimientos
        )
        inyeccion_flujos_directos = flujos_directos.calcular_inyeccion_flujos_directos(
            matriz_inyecciones, flujos_directos_df
        )
        status.update(label="Yacimientos/hubs listos ✅")

    with st.status("Tabla total y modelado de plantas...", expanded=False) as status:
        tabla_total_yacimientos = tabla_total.construir_tabla_total(
            inyeccion_yacimientos_areas, coefs_inyeccion_area, premisas_areas,
            propiedades, compuestos=None, periodo=periodo, inyeccion_std=inyeccion_std,
        )
        tabla_total_flujos_directos = tabla_total.construir_tabla_total(
            inyeccion_flujos_directos, coefs_inyeccion_area, premisas_areas,
            propiedades, compuestos=None, periodo=periodo,
        )
        tabla_mega, tabla_tty_dp = modelado_plantas.modelar_planta(
            matriz_inyecciones, tabla_total_yacimientos, tipo=None
        )
        status.update(label="Tabla total y modelado listos ✅")

    with st.status("Correcciones (dominio)...", expanded=False) as status:
        correcciones = dom_retenidos.calcular_correcciones(
            volumen_total=None,  # TODO: completar con el DataFrame/valor real
            retenido=retenidos_rtp,
            gas_rico_in=None,
            capacidad=capacidad,
            periodo=periodo,
            fecha_random=periodo,  # ver nota Fase 7 del roadmap: revisar si son el mismo valor
            propiedades=propiedades,
            presion_base=None, constante_gas=None, temperatura_base=None,
            etano=None, propano=None, butanos=None, gasolina=None,
        )
        status.update(label="Correcciones calculadas ✅")

    resultados = {
        "inyeccion_std": inyeccion_std,
        "inyeccion": inyeccion_df,
        "matriz_inyecciones": matriz_inyecciones,
        "inyeccion_area": inyeccion_area,
        "yacimientos_areas": yacimientos_areas,
        "inyeccion_yacimientos_areas": inyeccion_yacimientos_areas,
        "inyeccion_detalles_hubs_areas": detalles_hubs_areas,
        "inyeccion_flujos_directos": inyeccion_flujos_directos,
        "tabla_total_yacimientos": tabla_total_yacimientos,
        "tabla_total_flujos_directos": tabla_total_flujos_directos,
        "tabla_mega": tabla_mega,
        "tabla_tty_dp": tabla_tty_dp,
        "correcciones": correcciones,
    }

    if guardar_csvs:
        for nombre, df in resultados.items():
            if isinstance(df, pd.DataFrame):
                df.to_csv(f"{nombre}.csv", index=False)

    return resultados


if run:
    try:
        st.session_state["resultados"] = ejecutar_pipeline(
            input_path, periodo_ts, capacidad
        )
        st.sidebar.success("Pipeline ejecutado correctamente.")
    except Exception as e:
        st.sidebar.error(f"Error corriendo el pipeline: {e}")
        st.exception(e)

# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------

resultados = st.session_state.get("resultados")

if resultados is None:
    st.info("Configurá los parámetros en la barra lateral y apretá **Ejecutar pipeline**.")
else:
    tablas = {k: v for k, v in resultados.items() if isinstance(v, pd.DataFrame)}
    correcciones = resultados.get("correcciones")

    tabs = st.tabs(list(tablas.keys()) + ["Correcciones"])

    for tab, (nombre, df) in zip(tabs[:-1], tablas.items()):
        with tab:
            st.subheader(nombre)
            st.dataframe(df, use_container_width=True)
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False)
            st.download_button(
                f"⬇️ Descargar {nombre}.csv",
                data=csv_buffer.getvalue(),
                file_name=f"{nombre}.csv",
                mime="text/csv",
                key=f"download_{nombre}",
            )

    with tabs[-1]:
        st.subheader("Correcciones (valores de dominio, no tabulares)")
        if correcciones is not None:
            st.json(
                correcciones if isinstance(correcciones, dict) else correcciones.to_dict()
            )
            # Alerta de capacidad — TODO: reemplazar por la condición real
            # que hoy imprime el mensaje de alerta en primero.py.
            if isinstance(correcciones, dict) and correcciones.get("capcap", 0) > capacidad:
                st.warning("⚠️ Se superó la capacidad configurada.")
        else:
            st.write("Sin datos.")

```