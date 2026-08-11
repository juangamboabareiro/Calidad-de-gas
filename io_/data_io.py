import pandas as pd
from config import PATH_INPUTS
###  Ver INPUTS en Data_Dictionary.md

#Areas
mapa = pd.read_excel(PATH_INPUTS, sheet_name="Mapa", index_col="Num")
#Coeficientes para transformacion 9300 -> STD
coeficientes = pd.read_excel(PATH_INPUTS, sheet_name="Coeficientes")
#Valores de forecast para 9300
inyeccion_9300 = pd.read_excel(PATH_INPUTS, sheet_name="Inyeccion-9300")
#Valores de cromatografias
premisas_areas = pd.read_excel(PATH_INPUTS, sheet_name="Premisas-Areas")
#Propiedades de Gas Natural GPA
propiedades = pd.read_excel(PATH_INPUTS, sheet_name="Propiedades")
#Constantes de gas [Probablemente las considere ctes globales en el codigo a futuro]
constantes_GAS = pd.read_excel(PATH_INPUTS, sheet_name="Constantes-GAS")
#Matriz de origen-destino de plantas y gasoductos
matriz_inyecciones = pd.read_excel(PATH_INPUTS, sheet_name="Matriz-Inyecciones", index_col="Num")
#Estos 3 es agarrar inyeccion 2030 y desarmarla segun tipo
flujos_directos = pd.read_excel(PATH_INPUTS, sheet_name="Flujos-Directos")
yacimientos = pd.read_excel(PATH_INPUTS, sheet_name="Yacimientos")
detalles_hubs = pd.read_excel(PATH_INPUTS, sheet_name="Detalles-HUBs")
#Dataset de coeficientes dinamicos de Inyeccion Area
coefs_inyeccion_area = pd.read_excel(PATH_INPUTS, sheet_name="Coefs-Iny-Areas")
#Listado de HUBs
plantas_yacimientos = pd.read_excel(PATH_INPUTS, sheet_name="Plantas-Yacimientos")
#Porcentaje de Retenidos RTP
retenidos_RTP = pd.read_excel(PATH_INPUTS, sheet_name="Retenidos-RTP")
