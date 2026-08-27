# Linaje de datos

Cada dato desde la celda de `inputs.xlsx` hasta donde termina en el pipeline.
Es la referencia para migrar, auditar o responder "¿de dónde salió este
número?".

Relevado 2026-08-25 sobre `inputs.xlsx`, período 01-2025. El *qué significa*
está en `dominio.md`; acá va el *de dónde viene y a dónde va*.

> **Los nombres de hoja están hardcodeados en `io_/loaders.py`.** Renombrar una
> hoja rompe la carga. `tests/test_integracion.py::TestHojasDelExcel` lo
> verifica con mensaje claro.

---

## 0. Qué pasa al cargar (todas las hojas)

`io_/loaders.py` garantiza que **la clave ya es confiable** al salir del loader:

1. **Encabezados canonizados** — `"Inyeccion"` sin tilde o `"AREA "` con espacio
   llegan como la constante de `domain/columnas.py`.
2. **Columna `Area` canonizada** — `normalizar()` (minúsculas, sin tildes, solo
   alfanuméricos) + tabla de alias `datos/alias_areas.csv`.

Consecuencia: **aguas abajo del loader, `"Fortín de Piedra"` es
`"fortindepiedra"`** en todas partes. Los nombres originales solo existen
pasando `canonizar_area=False`.

⚠️ `datos/alias_areas.csv` **no está en el repo**: la tabla de alias carga
vacía y no resuelve ninguna equivalencia. Si un área figura con dos nombres en
hojas distintas, hoy no se unen.

Lo que los loaders **no** hacen: rellenar nulos, filtrar, calcular. Eso es de
`pipeline/preprocesamiento.py`.

---

## 1. Hoja por hoja

### `Inyeccion-9300` → `inyeccion_9300` (serie base)

| | |
|---|---|
| Loader | `load_inyeccion_9300` |
| Forma | `Area`, `Cuenca` + una columna por mes |
| Unidad | volumen a 9300 kcal/m³ |
| Va a | `calcular_inyeccion_std`: se divide por `Coeficientes` para pasar a estándar |
| Nulos | 3.494 celdas vacías → se rellenan con 0 (esperado) |

### `Coeficientes` → `coeficientes`

121 filas × 157 columnas (`Area` + meses desde 2020). Divide a
`Inyeccion-9300` elemento a elemento. Un coeficiente 0 con volumen ≠ 0 hace
**desaparecer** ese volumen — el pipeline lo cuenta e imprime al correr. Trae 1
fila duplicada exacta que se descarta.

### `Mapa` → `mapa`

Catálogo `Num` → `Area` (77 filas). Índice `Num`.

### `Plantas-Yacimientos` → `plantas_yacimientos`

`Area` → `HUB` (102 filas). Es el lookup que agrega HUB a la inyección y a los
yacimientos. Áreas sin HUB caen al default `"Otros"`.

### `Yacimientos` → `yacimientos` (inyección primaria)

| | |
|---|---|
| Forma | `Inyección`, `Area` + una columna por destino (VMN, VMS, MEGA…) |
| Va a | `calcular_inyeccion_yacimientos_areas` → `Total Yacimientos` |
| Estado | 28 áreas sin volumen: 19 sin inyección primaria (ok), 9 con inyección pero sin ese gasoducto — el pipeline lista los pares al correr |

### `Flujos-Directos` → `flujos_directos`

Misma forma que Yacimientos (22 filas). Orígenes que inyectan directo a un
gasoducto. ⚠️ Incluye orígenes que **no son áreas** (`tty`, `mega`, `bdp`,
`vmliq`) — ver §3.

### `Detalles-HUBs` → `detalles_hubs`

`Gasoducto`, `Area` + destinos (23 filas). Detalle por HUB →
`Total Detalles HUBs`.

### `Matriz-Inyecciones` → `matriz_inyecciones`

| | |
|---|---|
| Forma | **ancha**: una columna por destino, las áreas como *valores* |
| Destinos (20) | BdP · NEUI · VMN · CO (Troncal) · NEUII · GPM · GPA (a MEGA) · MEGA · TOTAL - APE / ASR · Pampa EM - BM · VMS · TBX El Porton · Otros · YPF - RDM · CO (Paralelo) · GPA (a Chile) · Pampa SCH · VM LIQ · TTY · TTY-PC |
| Particularidad | como las áreas son valores a lo ancho, el loader **no puede** canonizarlas; queda para `preprocesamiento` después del melt |

**Dos versiones circulan y no son intercambiables:**

- la **melteada** (`inputs["matriz_inyecciones"]`) — para los merges del pipeline;
- la **cruda y ancha** (`load_matriz_inyecciones(path)`) — va en `comunes` y
  `io_plantas` la usa como `matriz[nombre_planta]` para validar los orígenes
  del pool. **No reemplazar una por otra.**

`TTY` y `MEGA` son los `nombre_pool` de las plantas: la columna con la que se
filtra su gas. `TBX El Porton` y `VM LIQ` son destinos reales sin planta
modelada todavía.

### `Coefs-Iny-Areas` → `coefs_inyeccion_area`

160 filas × 98 columnas: `Area`, `Gasoducto` + meses de 2025+. Coeficiente de
reparto por ruta y mes. Se consulta al período considerado al armar las tablas
totales.

### `Premisas-Areas` → `premisas_areas` → `premisas_por_ruta` / `premisas_por_clave`

| | |
|---|---|
| Forma | `Area`, `Sufijo`, `Salida` + 14 compuestos + extras (133 filas) |
| Unidad | fracción molar (cada fila suma 1) |
| Transformación | `preparar_premisas` la parte en dos tablas: por ruta `(Area, Gasoducto)` y por clave `Area+Sufijo` |
| Estado | la tabla **por ruta hoy no matchea nada** (columna de destino vacía): todo cae al fallback. HALLAZGO-5. |
| Dato sucio | `aguadadecastro` cargada dos veces con cromatografías distintas y sin sufijo: se toma la primera, igual que el VLOOKUP del Excel |

### `Sufijos-Planta` → `sufijos_planta`

Clave concatenada `Area-Gasoducto` → sufijo (`Otra`/`Planta`/`TBX`), 9 pares +
encabezado. Dos fragilidades conocidas:

- la hoja **no tiene fila de encabezado real** (la primera fila de datos quedó
  como nombre de columna; `cargar_sufijos_planta` lo detecta comparando contra
  `SUFIJOS_CONOCIDOS`);
- el corte de la clave es por el **primer guion**: se rompe si un área llega a
  tener guion en el nombre (`validar_sufijos` lo chequea).

### `Propiedades` → `propiedades`

18 compuestos × 22 propiedades (`Peso molecular`, `Factor b`, `PCS [MJ/m3]`…).
Alimenta `calcular_propiedades_gas` y `calcular_retenidos`.

### `Constantes-GAS` → constantes de `domain/ctes_gas.py`

| Columna Excel | Constante |
|---|---|
| `Temperatura Base [°C]` | `TEMPERATURA_BASE` |
| `Presion Base [kPa]` | `PRESION_BASE` |
| `Cte. GAS [m3.kPa/(K.kmol)]` | `CONSTANTE_GAS` |
| `Conversion` | `CONVERSION` |

⚠️ Se leen **al importar el módulo** (`load_constantes_gas(PATH_INPUTS)` a
nivel de módulo). Si falta una columna, el import del paquete explota; si se
cambia el path después del primer import, no se relee sin `importlib.reload`.
Ver `decisiones/0004`.

No vienen del Excel: `DENSIDAD_AIRE=1.225`, `CONVERSION_BARRILLES_KGD=6.29`,
`MMBtu=252074`, y la definición de `COMPUESTOS` con sus cortes.

### `Retenidos-RTP` → `retenidos_rtp`

| | |
|---|---|
| Forma | `Planta` + 14 compuestos + `Consumo` (4 filas) |
| Unidad | fracción retenida 0–1 (**no** porcentaje) |
| Catálogo `Planta` | `TBX` · `Dew point` · `TBX MEGA` · `TBX EL PORTON` |

⚠️ Los tres primeros están **hardcodeados como strings literales** en
`registro_base()`. Renombrar uno en el Excel deja esa planta con retención cero,
sin error. El mapeo a nombres de UI:

| Excel | UI / registro |
|---|---|
| `TBX` | TTY - TBX |
| `Dew point` | TTY - Dew Point |
| `TBX MEGA` | MEGA |
| `TBX EL PORTON` | (sin planta todavía) |

---

## 2. El flujo completo

```
Inyeccion-9300 ─┬─ (÷ Coeficientes) → inyeccion_std
                │        │ (promedio por año, + HUB de Plantas-Yacimientos)
                │        ▼
                │   inyeccion ── (× Matriz-Inyecciones, melteada) → inyeccion_area
                │                                                        │
Yacimientos ────┼──────────────► inyeccion_yacimientos_areas ◄───────────┘
Detalles-HUBs ──┼──────────────► detalles_hubs_areas
Flujos-Directos ┴──────────────► inyeccion_flujos_directos
                                        │
        (período + Coefs-Iny-Areas + cromatografía de Premisas/Sufijos)
                                        ▼
              Total Yacimientos · Total Flujos Directos · Total Detalles HUBs
                                        │  (+ z, densidad, PCS, IW)
                                        ▼
        pools de planta (filtro Gasoducto == nombre_pool, dos fuentes)
                                        ▼
                          cascada: TBX → DP → MEGA
```

Columnas que nacen en el pipeline (no vienen de ninguna hoja):

| Columna | Nace en | Qué es |
|---|---|---|
| `Volumen_inyectado` | tablas totales | volumen del período para ese `(Area, Gasoducto)` |
| `Sufijo`, `Clave_croma` | `cromatografia` | claves de búsqueda de la premisa |
| `z`, `densidad`, `PCS`, `IW` | `calcular_propiedades_gas` | ver dominio §4.3 |
| `Origen_tabla` | `armar_input_planta` | `flujos_directos` / `yacimientos` / `derivacion` |
| `Volumen_pool` | `modelar_planta` | volumen del pool antes del reparto pro-rata |

⚠️ La fila de una derivación no pasa por `armar_input_planta` y llega sin
`Origen_tabla`: sin el `fillna("derivacion")`, el traspaso DP→MEGA desaparece
del panel de orígenes.

---

## 3. Discrepancias y datos sucios conocidos

En orden de impacto (detalle y verificación en `HALLAZGOS.md`):

| # | Qué | Dónde |
|---|---|---|
| 1 | 73% del volumen de `Total Flujos Directos` sin cromatografía: los orígenes `tty`, `mega`, `bdp`, `vmliq` no son áreas | HALLAZGO-1 |
| 2 | 17 filas con volumen negativo; el pool de MEGA incluye −1.371 (15%) | HALLAZGO-2 |
| 3 | `aguadadecastro` duplicada con cromatografías distintas | §1 Premisas |
| 4 | 19 áreas sin destino en la matriz, 6 con volumen (3.214 en total) | reporte del pipeline |
| 5 | `FACTOR_MMm3_A_UNIDAD_VOLUMEN = 1000` sin confirmar contra el Excel original | HALLAZGO-4 |
| 6 | Mes 10 sin estación asignada | `dominio.md` §6 |
| 7 | Alias de áreas: el CSV no existe, la tabla carga vacía | §0 |
