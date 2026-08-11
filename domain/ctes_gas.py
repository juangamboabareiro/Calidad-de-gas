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

