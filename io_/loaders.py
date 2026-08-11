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