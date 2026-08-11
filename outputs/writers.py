from pathlib import Path
import config

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"

def guardar(df, nombre, activar=config.GUARDAR_CSVS):
    if activar:
        ruta = OUTPUTS_DIR / f"{nombre}.csv"
        df.to_csv(ruta, index=False)
