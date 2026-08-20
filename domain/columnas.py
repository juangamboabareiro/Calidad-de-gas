"""
Nombres de columna usados en todo el pipeline.

Motivo: escribir el string suelto en cada archivo hace que un typo
("Inyeccion" sin tilde, "area" en minuscula) falle en silencio dentro de un
merge en vez de romper. Usando constantes, el typo revienta como NameError
en el momento y el editor te autocompleta.

Convencion: la constante se llama COL_<algo> y su valor es EXACTAMENTE el
nombre que tiene la columna en el Excel de inputs.
"""

# --- claves de cruce ---
COL_AREA = "Area"
COL_GASODUCTO = "Gasoducto"
COL_CUENCA = "Cuenca"
COL_HUB = "HUB"

# --- atributos ---
COL_INYECCION = "Inyección"      # ojo: lleva tilde en el Excel
COL_VOLUMEN = "Volumen"
COL_VOLUMEN_INYECTADO = "Volumen_inyectado"
COL_DESTINO = "Destino"
COL_COEF_INYECCION = "Coef_Inyeccion"

# --- temporales ---
COL_PERIODO = "Periodo"
COL_ANIO = "Anio"
COL_MES = "Mes"
COL_ESTACION = "Estacion"


# --- valores por defecto (antes eran strings magicos repetidos) ---
HUB_DEFAULT = "Otros"
INYECCION_DEFAULT = "Primaria"
