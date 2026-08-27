# Hallazgos

Cosas que aparecieron al escribir los tests y correr el pipeline contra
`inputs.xlsx` (período 01-2025). Ordenadas por impacto.

Cada una dice **qué se observó**, **cómo verificarlo** y **qué habría que
decidir**. No toqué código de producción: son para que decidas vos.

---

## HALLAZGO-0 · `config.py` define `MAX_DERIVACION_TTY_TBX_A_TTY_DP` dos veces, y la segunda multiplica de más

**Severidad:** alta (bug latente, hoy no se manifiesta)

`config.py` define la constante dos veces. Gana la segunda:

```python
# línea 31 — correcta
MAX_DERIVACION_TTY_TBX_A_TTY_DP = CAPACIDAD_TTY_DP - CAPACIDAD_BASE_CONVERTIBLE_TBX
#   = 28.000 - 13.200 = 14.800  →  14,8 MMm³/d ✅

# línea 43 — pisa a la anterior
MAX_DERIVACION_TTY_TBX_A_TTY_DP = (CAPACIDAD_TTY_DP - CAPACIDAD_BASE_CONVERTIBLE_TBX) * FACTOR_MMm3_A_UNIDAD_VOLUMEN
#   = 14.800 × 1000 = 14.800.000  →  14.800 MMm³/d ❌
```

`CAPACIDAD_TTY_DP` y `CAPACIDAD_BASE_CONVERTIBLE_TBX` **ya vienen multiplicadas
por el factor** (líneas 20-23). La segunda definición las multiplica otra vez:
el tope queda 1000× más grande, o sea prácticamente infinito.

El mismo bloque hace lo correcto para el otro tope
(`MAX_DERIVACION_TTY_DP_A_MEGA = 5 → 5000`), porque ese partía de un número
crudo. El patrón fue "agregar el factor a las dos", pero solo una lo necesitaba.

**Por qué no se ve hoy:** con los datos actuales TBX no genera sobrante
(ver HALLAZGO-3), así que el tope nunca se activa. Escenarios 2 y 3 de la
verificación dan idéntico. Se manifiesta apenas TBX empiece a llenarse.

**Cómo verificarlo:**
```python
import config
print(config.MAX_DERIVACION_TTY_TBX_A_TTY_DP)   # 14800000.0
```

**A decidir:** borrar el bloque duplicado de las líneas 40-43 y dejar una sola
definición. Ojo con el comentario `# CONFIRMAR` de la línea 36, que ya marcaba
esta zona como dudosa.

---

## HALLAZGO-1 · El 73% del volumen de `Total Flujos Directos` no tiene cromatografía

**Severidad:** alta (afecta resultados, pero no los de la cascada actual)

```
 Area      Gasoducto      Volumen_inyectado
 tty       CO (Paralelo)           1.229,85
 mega      CO (Paralelo)          10.829,63
 tty       NEUII                   4.919,40
 bdp       BdP                     3.401,00
 vmliq     TTY-PC                     71,78
                          ─────────────────
                                  20.451,66   = 73,4% de la tabla (27.864,29)
```

Los "orígenes" `tty`, `mega`, `bdp`, `vmliq` **no son áreas**: son plantas y
gasoductos. Por eso no están en `Premisas-Areas` y la búsqueda por
`Area + Sufijo` no los encuentra. Su cromatografía no puede salir de esa hoja
— tendría que salir del gas residual del modelo de planta.

Esto es exactamente la pregunta abierta que ya estaba anotada en el README:

> *"Hojas HUB's. ¿Cómo es la lógica y sobre producción total? Cromas gasoductos"*
> *"Los datos de cromato usados para TTY DP no son vmn y vms eso hay que cambiarlo"*

**Alcance real hoy:** esas filas tienen `Gasoducto` ∈ {BdP, CO (Paralelo),
NEUII, TTY-PC}, y los pools de planta se filtran por `Gasoducto ∈ {TTY, MEGA}`.
La intersección es vacía → **no contaminan los pools de TBX, DP ni MEGA**. Los
números de la cascada están bien. Lo que está incompleto es la tabla de flujos
directos como producto en sí.

**Cómo verificarlo:** `pytest -m integracion -k cromatografia` (sale XFAIL).

**A decidir:** de dónde sale la cromatografía de un gasoducto cuyo origen es
una planta. Probablemente el `gas_residual_OUT` de esa planta, lo que implica
resolver la cascada **antes** de completar esta tabla — o sea una dependencia
circular que hay que ordenar.

---

## HALLAZGO-2 · Diecisiete filas con volumen inyectado negativo, y el pool de MEGA se come 1.371

**Severidad:** alta

> **Corrección.** Al principio reporté "4 áreas, -107,20". Eso era solo
> `Total Yacimientos`. Al extender el chequeo a las tres tablas totales el
> cuadro es bastante peor.

| Tabla | Filas | Negativas | Suma negativa | Suma positiva | Neto |
|---|---|---|---|---|---|
| Total Yacimientos | 142 | 4 | −107,20 | 82.245,91 | 82.138,71 |
| **Total Flujos Directos** | 32 | **8** | **−51.877,70** | 79.741,99 | 27.864,29 |
| Total Detalles HUBs | 17 | 5 | −11.489,24 | 24.550,79 | 13.061,55 |

En `Total Flujos Directos` los negativos son **el 65% de la masa positiva**. Los
más grandes:

```
 Area        Gasoducto        Volumen_inyectado
 gpm         GPM                    -29.483,16
 mega        NEUII                  -10.927,84
 neuii       NEUII                   -5.923,60
 tty         GPM                     -3.931,28
 pampasch    MEGA                    -1.172,93
```

Y en Detalles HUBs, `aguadapichanaeste → TOTAL` da −8.500,00 exacto, que tiene
pinta de ser una resta deliberada (¿un descuento cargado con signo?) más que un
error de coeficiente.

**Impacto en la cascada:** el pool de MEGA incluye **−1.371,44** de volumen
negativo (−0,50 de Yacimientos y −1.370,93 de Flujos Directos). El pool de TTY
no tiene negativos.

Sobre un pool de MEGA de 7.704,41, ese −1.371 es el **15%**: no es despreciable.
Como `lgn_unitario = LGN del pool / volumen del pool`, un pool subestimado
**infla** el `lgn_unitario` y por lo tanto **reduce** el `vol_maximo` de MEGA.

**Cómo verificarlo:**
```python
for nombre, tabla in estado["tablas"].items():
    v = tabla["Volumen_inyectado"]
    print(nombre, len(v[v < 0]), v[v < 0].sum())
```

**A decidir:** hay que separar dos casos que probablemente estén mezclados.

1. **Negativos que son un artefacto de cálculo** (restas de coeficientes) →
   corregir la fórmula o `clip(lower=0)`.
2. **Negativos que son un dato con signo deliberado** — que un origen llamado
   `gpm` tenga −29.483 en el gasoducto `GPM`, o `mega` −10.927 en `NEUII`,
   sugiere que se está modelando gas que **sale** de ese nodo, no que entra. Si
   es así el signo es correcto y lo que falta es tratarlo como flujo saliente en
   vez de sumarlo al pool.

Es la misma familia de preguntas que el HALLAZGO-1: qué significa que una planta
o un gasoducto aparezca como "Area". Conviene resolver los dos juntos.

---

## HALLAZGO-3 · Los datos actuales no ejercitan la cascada

**Severidad:** media (riesgo de tests que no prueban nada)

Con `inputs.xlsx` y el `config.py` de hoy:

| | vol_disponible | vol_asignado | sobrante | derivado | bypass |
|---|---|---|---|---|---|
| TTY - TBX | 4.557,09 | 4.557,09 | 0,00 | 0,00 | 0,00 |
| TTY - Dew Point | **0,00** | 0,00 | 0,00 | 0,00 | 0,00 |
| MEGA | 7.704,41 | 7.704,41 | 0,00 | 0,00 | 0,00 |

TBX absorbe todo el pool TTY (4.557 contra un `vol_maximo` de 34.000), así que
**TTY-DP queda en cero** y no se ejercita ni el traspaso, ni la derivación, ni
el bypass. Un test que solo corra el caso base pasaría aunque esa lógica
estuviera rota.

Por eso `TestEscenariosForzados` fuerza capacidades para recorrer esos caminos.
Verificado que funcionan:

- evacuación TBX = 500 → sobrante 1.544,63, todo derivado a DP ✅
- + tope 1.000 → derivado 1.000, bypass 544,63 ✅
- pre-PM → TBX en 0, el pool entero (4.557,09) pasa a DP ✅

**A decidir:** vale la pena guardar un `inputs_test.xlsx` reducido que sí
ejercite la cascada, o dejar los escenarios forzados como están.

---

## HALLAZGO-4 · Los pools son chicos comparados con las capacidades

**Severidad:** a confirmar (posible problema de unidades)

| | Pool | Capacidad de ingreso |
|---|---|---|
| TTY | 4,56 MMm³/d | 34 MMm³/d |
| MEGA | 7,70 MMm³/d | 43 MMm³/d |

Las plantas trabajan al 13% y 18% de su capacidad de ingreso. Puede ser
correcto (el período 01-2025 es real), pero se cruza con el comentario que ya
está en `config.py`:

```python
# CONFIRMAR: las capacidades de ingreso estan en MMm3/d (28, 34, 43) pero
# Volumen_inyectado viene de los inputs en otra escala.
```

**A decidir:** confirmar contra el Excel original si `FACTOR_MMm3_A_UNIDAD_VOLUMEN = 1000`
es el factor correcto. No lo pude verificar solo con el código.

---

## HALLAZGO-5 · La búsqueda de cromatografía por ruta nunca matchea

```
[preparar_premisas] 0 premisas por ruta, 123 por clave
[tabla_total_yacimientos]      0 por ruta, 122 por clave, 20 sin resolver
[tabla_total_flujos_directos]  0 por ruta,  25 por clave,  7 sin resolver
```

Las dos etapas de búsqueda funcionan, pero **la primera (por `(Area, Gasoducto)`)
no matchea ni una fila**: todo cae al fallback por `Area + Sufijo`.

Es el comportamiento anticipado en el docstring de `cromatografia.py`:

> *"Mientras la columna de destino de las premisas de gasoducto siga vacía, esas
> filas caen a la segunda etapa con sufijo vacío"*

O sea que no es un bug — es una rama de código que hoy está **muerta** y que
ningún test de datos reales puede cubrir. Los tests unitarios de `clave_cruce`
la cubren indirectamente.

**A decidir:** si la columna de destino de las premisas de gasoducto se va a
llenar alguna vez. Si no, se puede simplificar a una sola etapa.

---

## HALLAZGO-6 · Dos implementaciones paralelas de la cascada

**Severidad:** alta como riesgo estructural (hoy no divergen)

| Camino | Quién lo usa | Módulos |
|---|---|---|
| Legacy | `app.py`, `main.py` → tabs Resumen y Cascada | `plantas/TTY.py`, `plantas/MEGA.py` |
| Genérico | `ui/tab_plantas.py` → tab sandbox | `registro.py`, `cascada.py`, `planta.py` |

**Verificado: hoy dan exactamente lo mismo** (máx. |diferencia| = 0,000000 en
los 8 campos de flujo, las 3 plantas, cromatografías y retenidos).

Pero nada lo garantiza: si alguien toca uno de los dos, los números del sandbox
dejan de coincidir con los del resumen y no salta nada.
`TestEquivalenciaCascadas` es lo único que lo impide, y hay que correrlo.

**A decidir:** migrar `app.py` a `resolver_cascada` y borrar
`plantas/TTY.py` + `plantas/MEGA.py`. El docstring de `planta.py` dice que la
equivalencia ya se había demostrado con un `test_registro_plantas.py` que **no
está en el repo** — si existe en otro lado, conviene recuperarlo.

---

## Menores

| # | Qué | Dónde |
|---|---|---|
| 7 | `main.py` tiene código exploratorio al final (prints sueltos, `geopandas`, lectura de shapefiles). No corre sin geopandas y ensucia el orquestador. | `main.py` líneas ~230-288 |
| 8 | `main.py` sigue usando los `modelar_*` legacy: no refleja la arquitectura nueva. Además importa `cromatografia` dos veces. | `main.py` |
| 9 | `TBX_EP.py` y `VM_LIQ.py` están **vacíos** (0 bytes), pero `TBX El Porton` y `VM LIQ` son destinos reales de la matriz y `Retenidos-RTP` trae `TBX EL PORTON`. Trabajo empezado sin terminar. | `pipeline/plantas/` |
| 10 | `TTY_DP.py` y `TTY_TBX.py` definen funciones que **no importa nadie**: código muerto. | `pipeline/plantas/` |
| 11 | `arq.md` (1379 líneas) contiene un borrador viejo de `app.py` con `# TODO` y una estructura de carpetas que ya no es la real. Confunde más de lo que ayuda. | `arq.md` |
| 12 | `datos/alias_areas.csv` no está en el repo. `cargar_alias` devuelve `{}` y sigue, pero entonces la tabla de alias no está haciendo nada. | `io_/loaders.py` |
| 13 | `outputs/*.csv.csv` — doble extensión, y son outputs versionados. | `outputs/` |
| 14 | `Sufijos-Planta` se lee sin encabezado real: la primera fila de datos (`Fortin de Piedra-VMS`, `Otra`) quedó como nombre de columna. `cargar_sufijos_planta` lo maneja, pero es frágil. | hoja del Excel |
