"""Smoke test del tab con un Streamlit falso: ejercita todos los caminos."""
import sys, types
import numpy as np, pandas as pd
sys.path.insert(0, ".")

# ---- stubs del pipeline ----
POOLS = {"TTY": 30000.0, "MEGA": 20000.0}
GRUPOS = ["etano", "propano", "butanos", "gasolina"]
COMPUESTOS = ["metano", "etano", "propano", "ibutano", "nbutano", "gasolina"]

def io_plantas(matriz_inyecciones, calcular_retenidos, tabla_total_flujos_directos,
               tabla_total_yacimientos, propiedades, compuestos, retenidos_planta,
               nombre_planta, derivaciones=None):
    vol = POOLS.get(nombre_planta, 0.0) + sum(float(d["vol_derivacion"]) for d in (derivaciones or []))
    tabla = pd.DataFrame({"Gasoducto": [nombre_planta], "Volumen_inyectado": [vol]})
    ret = retenidos_planta.iloc[0].astype(float)
    gr = pd.Series({c: 1/len(compuestos) for c in compuestos})
    return (tabla, gr, gr*(1-ret), pd.DataFrame([{c: .1 for c in compuestos}]),
            pd.DataFrame([{g: vol*1e-4 for g in GRUPOS}]))

m = types.ModuleType("pipeline.plantas.planta_template"); m.io_plantas = io_plantas
sys.modules["pipeline.plantas.planta_template"] = m
f = types.ModuleType("pipeline.plantas.flujo_plantas")
f.calcular_lgn_unitario = lambda v, rv: (float(np.asarray(rv).sum())/v) if v else 0.0
f.calcular_volumen_maximo = lambda lgn_unitario, CAPACIDAD_EVACUACION_PLANTA, CAPACIDAD_INGRESO_PLANTA=None: min(
    CAPACIDAD_EVACUACION_PLANTA/lgn_unitario if lgn_unitario > 0 else float("inf"),
    CAPACIDAD_INGRESO_PLANTA if CAPACIDAD_INGRESO_PLANTA is not None else float("inf"))
def _rep(vol_disponible, vol_maximo, MAX_DERIVACION_PLANTA_A_PLANTA=0.0):
    vd = max(float(vol_disponible), 0.0); va = min(vd, float(vol_maximo))
    sob = vd-va; der = min(sob, float(MAX_DERIVACION_PLANTA_A_PLANTA))
    return {"vol_disponible": vd, "vol_maximo": vol_maximo, "vol_asignado": va,
            "sobrante": sob, "vol_derivado": der, "bypass": sob-der, "ocupacion": None}
f.repartir_flujo_planta = _rep
sys.modules["pipeline.plantas.flujo_plantas"] = f

# ---- Streamlit falso ----
LLAMADAS = []
class Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False
class St(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = {}
        self.column_config = types.SimpleNamespace(
            TextColumn=lambda **k: None, NumberColumn=lambda **k: None,
            CheckboxColumn=lambda **k: None)
        self._boton_activo = None
    def __getattr__(self, name):
        if name in ("fragment", "experimental_fragment"):
            # Decorador: tiene que devolver algo invocable, como el real.
            return lambda fn=None, **k: (fn if fn is not None else (lambda g: g))
        def _f(*a, **k):
            LLAMADAS.append(name)
            if name in ("expander", "spinner", "status", "container", "form", "sidebar"):
                return Ctx()
            if name == "tabs":
                nombres = a[0] if a else []
                return [Ctx() for _ in nombres]
            if name == "toggle":
                return k.get("value", a[1] if len(a) > 1 else True)
            if name == "slider":
                return float(k.get("value", 0.0))
            if name == "columns":
                n = a[0] if a else 2
                n = len(n) if isinstance(n, (list, tuple)) else n
                return [_Col() for _ in range(n)]
            if name == "button": return k.get("key") == self._boton_activo
            if name == "checkbox": return k.get("value", a[1] if len(a) > 1 else True)
            if name == "radio":
                op = k.get("options", a[1] if len(a) > 1 else [])
                return op[k.get("index", 0)] if op else None
            if name == "selectbox":
                op = k.get("options", a[1] if len(a) > 1 else [])
                return op[0] if len(op) else None
            if name == "multiselect": return k.get("default", [])
            if name == "text_input": return k.get("value", "")
            if name == "number_input": return float(k.get("value", 0.0))
            if name == "color_picker": return k.get("value", "#000000")
            if name == "file_uploader": return None
            if name == "data_editor": return a[0] if a else pd.DataFrame()
            # `st.rerun` real corta el script con una excepcion de control. Acá
            # se registra y sigue: interesa verificar QUE se pidió el rerun y
            # que el resultado ya estaba guardado antes.
            if name == "rerun": return None
            return None
        return _f
class _Col(Ctx):
    def __getattr__(self, name):
        return getattr(sys.modules["streamlit"], name)

sys.modules["streamlit"] = St()
import streamlit as st

from pipeline.plantas.registro import registro_base, crear_planta, ConexionSalida, INFINITO
from pipeline.plantas.cascada import resolver_cascada
from ui.tab_plantas import panel_tab_plantas

PARAMS = dict(CAPACIDAD_EVACUACION_TTY_TBX=0.9, CAPACIDAD_EVACUACION_TTY_DP=0.4,
              CAPACIDAD_EVACUACION_MEGA=0.5, CAPACIDAD_TTY_TBX=34000,
              CAPACIDAD_TTY_DP=28000, CAPACIDAD_MEGA=43000,
              MAX_DERIVACION_TTY_DP_A_MEGA=5000, MAX_DERIVACION_TTY_TBX_A_TTY_DP=14800)
rtp = pd.DataFrame([{"Planta": p, **{c: v for c in COMPUESTOS}}
                    for p, v in [("TBX", .5), ("Dew point", .3), ("TBX MEGA", .7)]])
comunes = dict(matriz_inyecciones=None, calcular_retenidos=None,
               tabla_total_flujos_directos=None, tabla_total_yacimientos=None,
               propiedades=None, COMPUESTOS=COMPUESTOS)

# "produccion": la misma cascada base, para que el control tenga contra qué comparar
reg_prod = registro_base(PARAMS, rtp, COMPUESTOS, True)
_, flujos_prod = resolver_cascada(reg_prod, comunes)

comunes["tabla_total_yacimientos"] = pd.DataFrame([
    {"Area": "Chivo", "HUB": "H1", "Gasoducto": "VMN", "Volumen_inyectado": 600.0,
     **{c: 1/len(COMPUESTOS) for c in COMPUESTOS}},
    {"Area": "Chivo", "HUB": "H1", "Gasoducto": "MEGA", "Volumen_inyectado": 400.0,
     **{c: 1/len(COMPUESTOS) for c in COMPUESTOS}},
])
comunes["tabla_total_flujos_directos"] = pd.DataFrame([
    {"Area": "VMN", "HUB": "-", "Gasoducto": "TTY", "Volumen_inyectado": 600.0,
     **{c: 1/len(COMPUESTOS) for c in COMPUESTOS}},
])

resultados = {"comunes": comunes, "retenidos_rtp": rtp,
              "flujos_plantas": flujos_prod, "tbx_en_servicio": True}

# 1) sin resultado guardado
panel_tab_plantas(resultados, PARAMS)
assert "info" in LLAMADAS
print("OK estado inicial: pide resolver")

# 2) faltan claves -> mensaje de integracion, sin reventar
LLAMADAS.clear()
panel_tab_plantas({"flujos_plantas": flujos_prod}, PARAMS)
assert "error" in LLAMADAS
print("OK avisa si faltan `comunes` / `retenidos_rtp`")

# 3) correr con el registro intacto -> control en verde
LLAMADAS.clear(); st._boton_activo = "btn_correr_sandbox"
panel_tab_plantas(resultados, PARAMS)
st._boton_activo = None
assert "rerun" in LLAMADAS, "tras resolver hay que pedir rerun de app para redibujar la salida"
plantas, flujos = st.session_state["sandbox_resultado"]
assert list(flujos.index) == ["TTY - TBX", "TTY - Dew Point", "MEGA"]
print("OK corre, guarda el resultado y pide rerun")

# el rerun de verdad vuelve a entrar: ahora sí se dibuja la salida
LLAMADAS.clear()
panel_tab_plantas(resultados, PARAMS)
assert "success" in LLAMADAS, LLAMADAS
assert "graphviz_chart" in LLAMADAS and "dataframe" in LLAMADAS
print("OK en el rerun siguiente el control da idéntico a producción")

# 4) agregar una planta y volver a correr
reg = st.session_state["registro_plantas"]
reg["MEGA II"] = crear_planta("MEGA II", preset="MEGA", nombre_pool="MEGA II",
                              compuestos=COMPUESTOS, capacidad_evacuacion=0.3)
reg["TTY - Dew Point"].conexiones = [
    ConexionSalida("MEGA", 0.5, 5000.0, False),
    ConexionSalida("MEGA II", 0.5, INFINITO, False)]
LLAMADAS.clear(); st._boton_activo = "btn_correr_sandbox"
panel_tab_plantas(resultados, PARAMS)
st._boton_activo = None
plantas, flujos = st.session_state["sandbox_resultado"]
assert "MEGA II" in plantas and len(flujos) == 4
print("OK con planta agregada:", list(flujos.index))

# --- 4b) escenario prearmado: merge sin pisar las base ---
import json
from ui.plantas_editor import aplicar_escenario
from ui.escenarios import partir
reg_m = registro_base(PARAMS, rtp, COMPUESTOS, True)
cap_dp = reg_m["TTY - Dew Point"].capacidad_evacuacion
# El escenario ahora trae plantas Y gasoductos: hay que partirlo primero.
plantas_json, ductos_json = partir(json.load(open("escenarios/tbx_aguada.json")))
nuevas, parcheadas = aplicar_escenario(reg_m, plantas_json)
assert nuevas == 1 and parcheadas == 1
assert set(reg_m) == {"TTY - TBX", "TTY - Dew Point", "MEGA", "TBX Aguada"}
assert reg_m["TTY - Dew Point"].capacidad_evacuacion == cap_dp, "el parche no toca capacidades"
assert len(reg_m["TBX Aguada"].cromas_extra) == 2
print("OK escenario prearmado: merge, base intactas, 2 cromas incluidas")

# 5) la cascada revienta -> se muestra el error sin tumbar el tab
import pipeline.plantas.cascada as casc
orig = casc.resolver_cascada
import ui.tab_plantas as tp
def explota(*a, **k): raise ValueError("boom")
tp.resolver_cascada = explota
LLAMADAS.clear(); st._boton_activo = "btn_correr_sandbox"
panel_tab_plantas(resultados, PARAMS)
st._boton_activo = None
tp.resolver_cascada = orig
assert "error" in LLAMADAS and "exception" in LLAMADAS
assert "sandbox_resultado" not in st.session_state
print("OK una excepción se muestra y limpia el resultado viejo")

# --- 6) gasoductos: el tab llama al panel y las intervenciones se aplican ---
from pipeline.gasoductos.intervenciones import Intervencion
from ui.tab_plantas import _comunes_con_ductos

interv = [Intervencion("alta", "GNuevo", area_origen="Chivo",
                       planta_destino="TTY", volumen=250.0)]
efectivo, informe = _comunes_con_ductos(comunes, interv, COMPUESTOS)

assert efectivo is not comunes, "no se pueden pisar las tablas de producción"
assert comunes["tabla_total_yacimientos"].shape[0] == 2, "el original queda intacto"
assert efectivo["tabla_total_yacimientos"].shape[0] == 3
yac2 = efectivo["tabla_total_yacimientos"]
assert abs(yac2[yac2["Area"] == "Chivo"]["Volumen_inyectado"].sum() - 1000.0) < 1e-9
assert "GNuevo" in set(efectivo["tabla_total_flujos_directos"]["Area"])
print("OK ductos: se aplican sobre una copia y el área inyecta lo mismo")

sin_nada, informe_vacio = _comunes_con_ductos(comunes, [], COMPUESTOS)
assert sin_nada is comunes and informe_vacio is None
print("OK sin intervenciones se pasa `comunes` tal cual (sin copiar de gusto)")

LLAMADAS.clear(); st._boton_activo = "btn_correr_sandbox"
panel_tab_plantas(resultados, PARAMS)
st._boton_activo = None
assert "tabs" in LLAMADAS, "el editor tiene sub-tabs de plantas y ductos"
print("OK el tab dibuja los dos sub-paneles")

print("\nSMOKE OK")
