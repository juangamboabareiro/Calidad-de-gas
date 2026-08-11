import pandas as pd
import numpy as np
import unicodedata




def normalizar(texto):
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



# --- CONFIGURACIÓN ---
AREA_COL = "Area"
TOL = 1e-3  # tolerancia numérica


df_excel = pd.read_excel("validacion.xlsx", sheet_name="Yacimientos")
df_final = pd.read_csv("TABLA_TOTAL_YACS.csv")

# Copias
df_py = df_final.copy()
df_xl = df_excel.copy()

df_xl['Area'] = df_xl['Area'].apply(normalizar)



# Áreas comunes
areas_comunes = set(df_py[AREA_COL]) & set(df_xl[AREA_COL])

print(f"Áreas comunes: {len(areas_comunes)}")
print(f"Áreas solo en Python: {len(set(df_py[AREA_COL]) - areas_comunes)}")
print(f"Áreas solo en Excel: {len(set(df_xl[AREA_COL]) - areas_comunes)}")

# Filtrar áreas comunes
df_py = df_py[df_py[AREA_COL].isin(areas_comunes)]
df_xl = df_xl[df_xl[AREA_COL].isin(areas_comunes)]

# Merge para comparar
df_comp = df_py.merge(
    df_xl,
    on=AREA_COL,
    how="inner",
    suffixes=("_py", "_xl")
)

print(df_comp.head())

# Detectar columnas numéricas comunes
cols_comunes = (
    set(df_py.columns)
    .intersection(df_xl.columns)
    - {AREA_COL}
)

cols_numericas = [
    c for c in cols_comunes
    if pd.api.types.is_numeric_dtype(df_py[c])
    and pd.api.types.is_numeric_dtype(df_xl[c])
]

print("\nColumnas numéricas comparadas:")
print(cols_numericas)

# Resumen
resumen = []

for col in cols_numericas:

    diff = df_comp[f"{col}_py"] - df_comp[f"{col}_xl"]

    resumen.append({
        "columna": col,
        "max_diff_abs": diff.abs().max(),
        "mean_diff_abs": diff.abs().mean(),
        "coinciden": np.allclose(
            df_comp[f"{col}_py"],
            df_comp[f"{col}_xl"],
            atol=TOL,
            equal_nan=True
        )
    })

    errores = diff.abs() > TOL

    if errores.any():
        print(f"\n--- Diferencias en {col} ({errores.sum()} áreas) ---")
        print(
            df_comp.loc[
                errores,
                [AREA_COL, f"{col}_py", f"{col}_xl"]
            ].head(20)
        )

resumen = pd.DataFrame(resumen).sort_values("max_diff_abs", ascending=False)

print("\n================ RESUMEN ================\n")
print(resumen)

if resumen["coinciden"].all():
    print("\n✅ Todas las columnas numéricas coinciden dentro de la tolerancia.")
else:
    print("\n⚠️ Se encontraron diferencias.")