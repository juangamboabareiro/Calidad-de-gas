# Mapa de módulos

<!-- GENERADO por tools/mapa_modulos.py el 2026-08-27 — no editar a mano. -->
<!-- Regenerar: python tools/mapa_modulos.py --escribir -->

El *qué significa* está en `dominio.md`; acá va el *dónde está*.

## Árbol

```
app.py                                  # Interfaz Streamlit — Balance de Gas
config.py
main.py
domain/
  checks.py                             # Chequeos y merges instrumentados
  columnas.py                           # Nombres de columna usados en todo el pipeline
  ctes_gas.py
  normalizacion.py                      # Normalizacion de texto y clasificacion de estaciones
  propiedades_gas.py                    # calcular_propiedades_gas, calcular_retenidos, calcular_energia_total
io_/
  cromatografias_planta.py              # Cromatografias de planta cargadas por archivo APARTE
  loaders.py                            # Lectura de las hojas de inputs
pipeline/
  comunes.py                            # Operaciones que se repiten en yacimientos, detalles_hubs y flujo
  cromatografia.py                      # Asignacion de cromatografia a cada fila (Area, Gasoducto)
  detalles_hubs.py                      # Detalle de HUBs (hoja Detalles-HUBs)
  flujos_directos.py                    # Flujos directos de area a gasoducto (hoja Flujos-Directos)
  inyeccion_area.py                     # Promedio anual de inyeccion y asignacion de destinos
  inyeccion_std.py                      # Paso de inyeccion a 9300 kcal/m3 hacia volumen estandar
  preprocesamiento.py                   # Preparacion de los inputs crudos antes del pipeline de calculo
  tabla_total.py                        # query_volumen_tabla_total, query_coef_inyeccion_tabla_total, calcular_tabla_total_yacimientos, calcular_tabla_total_flujos_directos, +1
  yacimientos.py                        # Inyeccion primaria (hoja Yacimientos)
  plantas/
    MEGA.py                             # modelar_MEGA
    TBX_EP.py                           # (vacío)
    TTY.py                              # correccion_TTY, modelar_TTY
    TTY_DP.py                           # correccion_TTY_DP, modelar_TTY_DP
    TTY_TBX.py                          # correccion_TTY_TBX, modelar_TTY_TBX
    VM_LIQ.py                           # (vacío)
    cascada.py                          # Resolucion de la cascada: orden, traspaso de gas y grafo
    flujo_plantas.py                    # calcular_lgn_unitario, calcular_volumen_maximo, repartir_flujo_planta, calcular_DERIVACION
    planta.py                           # El modelo de planta, uno solo
    planta_template.py                  # armar_input_planta, io_plantas
    registro.py                         # Registro de plantas: la cascada como DATO, y `crear_planta` como
    reparto_proporcional.py             # Reparto del sobrante de una planta entre varios destinos, por pr
scripts/
  preparar_geo.py                       # Prepara la geodata local del mapa. Corre una sola vez, sin red
tools/
  mapa_modulos.py                       # Genera docs/mapa.md a partir del codigo, no de la memoria de nad
ui/
  __init__.py                           # Capa de presentación de la app Streamlit (esquemas SVG y explora
  diagnosticos.py                       # Captura de los mensajes de diagnostico del pipeline para mostrar
  esquemas.py                           # Esquema de bloques de una planta (SVG)
  mapa.py                               # Mapa de la red: areas, gasoductos y plantas sobre el territorio
  plantas_editor.py                     # Panel de configuracion de plantas
  tab_graphs.py                         # Tab "Graphs" — KPIs y series temporales
  tab_plantas.py                        # Tab "Plantas (sandbox)" para app.py
  tablas.py                             # Explorador de tablas + comparador contra el Excel de referencia
```

## Módulos que importan `config`

Si alguno lee constantes **a nivel de módulo**, tiene que estar en
`_actualizar_config_y_recargar` de `app.py` o los parámetros del
sidebar no lo afectan (ver `decisiones/0004`):

- `domain/ctes_gas.py`
- `pipeline/plantas/MEGA.py`
- `pipeline/plantas/TTY.py`
- `pipeline/plantas/TTY_DP.py`
- `pipeline/plantas/TTY_TBX.py`
- `pipeline/plantas/planta_template.py`
- `pipeline/preprocesamiento.py`

## Dependencias internas

| Módulo | Importa |
|---|---|
| `app.py` | `ui.diagnosticos`, `ui.esquemas`, `ui.mapa`, `ui.tab_graphs`, `ui.tab_plantas`, `ui.tablas` |
| `ui/tab_plantas.py` | `ui.plantas_editor` |

## Módulos vacíos (trabajo empezado sin terminar)

- `pipeline/plantas/TBX_EP.py`
- `pipeline/plantas/VM_LIQ.py`
