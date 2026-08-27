# Validaciones

Catálogo de todos los chequeos del proyecto: dónde vive cada uno, qué atrapa y
qué pasa cuando salta.

El modelo venía de un Excel que tenía celdas de control. Al migrar a Python esos
chequeos se perdieron y se reconstruyeron en capas separadas. Este documento es
el índice.

---

## Las cinco capas

```
1. CARGA          io_/loaders.py          canoniza encabezados y áreas
2. CRUCE          domain/checks.py        instrumenta cada merge
3. PIPELINE       pipeline/*.py           reporta filas sin resolver
4. CONFIGURACIÓN  plantas/registro.py     valida el registro antes de correr
5. RESULTADO      plantas/cascada.py      balance por eslabón
                  tests/                  invariantes y estructura del Excel
```

Regla general: **las capas 1-3 avisan y siguen** (imprimen por consola, que la
UI captura en el panel de diagnósticos); **la capa 4 bloquea** (errores que
impiden correr); **la capa 5 es aserción** (si falla, el resultado no es
confiable).

---

## Capa 1 — Carga

`io_/loaders.py` devuelve tablas cuya clave ya es confiable.

| Chequeo | Función | Qué atrapa |
|---|---|---|
| Canonizar encabezados | `canonizar_columnas` | `"Inyeccion"` sin tilde, `"AREA "` con espacio |
| Canonizar áreas | `canonizar_areas` | tildes, mayúsculas, espacios, + tabla de alias |

**No hace:** rellenar nulos, filtrar filas ni calcular. Eso es de
`preprocesamiento`.

> ⚠️ `datos/alias_areas.csv` no está en el repo. `cargar_alias` devuelve `{}` y
> el pipeline corre igual, pero la tabla de alias no está resolviendo ninguna
> equivalencia. Si existen áreas que figuran con dos nombres distintos en hojas
> distintas, hoy no se están uniendo.

---

## Capa 2 — Cruce

`domain/checks.py`. El problema que resuelve: **en pandas un merge mal hecho no
tira error**, devuelve un DataFrame válido con números mal.

| Función | Qué hace | Cuándo salta |
|---|---|---|
| `merge_validado` | envuelve el merge | cambió la cantidad de filas (duplicación); hay filas sin match (con ejemplos) |
| `merge_validado(validate="m:1")` | exige cardinalidad | **levanta `MergeError`**, no avisa |
| `avisar_duplicados` | clave repetida | correr **antes** de usar una tabla como lado derecho |
| `detectar_colisiones` | nombres distintos que normalizan igual | `"El Mangrullo"` vs `"El Mangrulló"` |

`VERBOSE = False` silencia todo (útil dentro de Streamlit o en los tests).
`reportar=False` silencia una sola llamada, para los merges donde las filas sin
match son esperables y su ruido tapa a los que sí importan.

**Un cambio en la cantidad de filas se avisa siempre**, aunque el llamador
silencie el resto: nunca es esperable.

---

## Capa 3 — Pipeline

Reportes por consola de cada etapa. En una corrida real hoy salen así:

| Origen | Mensaje típico | Interpretación |
|---|---|---|
| `preparar_premisas` | `1 areas cargadas dos veces con cromatografias DISTINTAS` | inconsistencia de la hoja (`aguadadecastro`). Se toma la primera, igual que el VLOOKUP. |
| `preparar_premisas` | `0 premisas por ruta, 123 por clave` | la etapa por ruta está muerta (HALLAZGO-5) |
| `coeficientes` | `1 filas duplicadas exactas, se descartan` | duplicado redundante, inocuo |
| `inyeccion_std` | `3494 celdas sin volumen, se rellenan con 0` | esperado |
| `inyeccion_area` | `19 areas sin destino en la matriz, 6 con volumen` | volumen que no llega a ningún destino |
| `yacimientos` | `28 areas sin volumen` | 19 sin inyección primaria (ok), 9 con inyección pero sin ese gasoducto |
| `tabla_total_*` | `N filas SIN cromatografia` | aportan gas y **cero LGN** |
| `input_planta` | `matriz vs destino - sin volumen: [...]` | orígenes declarados en la matriz que no aportan |

Funciones específicas:

- **`validar_sufijos`** — verifica que el corte de la clave concatenada por el
  **primer guion** haya dado nombres de área reales. Se rompe si algún día un
  área tiene guion en el nombre.
- **`validar_destinos_matriz`** (`preprocesamiento`) — destinos declarados que
  no existen.

---

## Capa 4 — Configuración

`validar_registro(registro) -> (errores, avisos)`. **Los errores bloquean la
corrida.**

### Errores

| Chequeo | Por qué es error |
|---|---|
| Destino inexistente | el gas se deriva a una planta que no está en el registro: desaparece |
| Autoconexión | una planta que se manda gas a sí misma |
| Ciclo | el gas no puede volver a una planta anterior de la cascada |
| Proporción negativa | no tiene sentido |
| Planta sin fuente de gas | `toma_volumen_del_pool=False` y nadie le deriva: queda en cero |
| Sin retenidos cargados | produciría cero LGN sin avisar |

### Avisos

| Chequeo | Por qué solo avisa |
|---|---|
| Derivación apagada con conexiones cargadas | es legal, pero todo el sobrante va a bypass |
| Proporciones que suman > 100% | se renormalizan hacia abajo |
| Retenidos fuera de 0-1 | probablemente estén cargados en % en vez de fracción |

También valida **`crear_planta`**, que falla temprano y con excepción ante:
nombre vacío, preset desconocido (listando los disponibles), feature desconocida
(un typo no puede pasar como si nada), retenidos con más de una fila o de tipo
irreconocible.

---

## Capa 5 — Resultado

### El invariante central

```
vol_disponible == vol_asignado + vol_derivado + bypass     (por eslabón)
```

`desvio_balance(flujos_df)` devuelve el máximo `|desvío|`. La app lo pinta en
verde si es `< 1e-6`; `main.py` lo tiene como `assert`.

El `vol_derivado` de un eslabón es el `vol_disponible` del siguiente, así que la
cadena cierra sin doble conteo. **Ojo:** `sum(tabla_mega)` incluye la fila de
derivación de DP, así que no se puede sumar con `sum(tabla_tty_dp)`.

### Otros invariantes (cubiertos por tests)

| Invariante | Dónde |
|---|---|
| `lgn_asignado = vol_asignado × lgn_unitario` | `TestBalanceCascada` |
| `lgn_asignado ≤ capacidad_evacuacion` | ídem — es la restricción activa |
| `vol_max × lgn_unitario = capacidad_evacuacion` | `TestLgnUnitario` |
| `sobrante = Σ derivados + bypass` | `TestRepartoProporcional` |
| gas residual ≤ gas rico de entrada | `TestBalanceCascada` |
| las fracciones molares suman 1 | `TestCoberturaDeCromatografia` |
| planta inactiva: no trata, no produce, no bypasea | `TestEscenariosForzados` |

---

## Capa 5b — Tests

Ver `tests/README.md`. Lo que agregan sobre lo anterior:

- **Estructura del Excel** — las 14 hojas, las 4 constantes de gas, las plantas
  de `Retenidos-RTP`, una sola fila por planta, fracciones en 0-1.
- **Equivalencia de las dos cascadas** — el test más importante mientras
  convivan `modelar_TTY`/`modelar_MEGA` y `resolver_cascada`.
- **Escenarios forzados** — porque los datos reales no ejercitan traspaso,
  bypass ni pre-PM (HALLAZGO-3).
- **Tests negativos** — cada chequeo tiene uno con datos rotos a propósito. Sin
  eso, un test verde no prueba que el chequeo chequee.

---

## Qué NO está validado

Huecos conocidos, en orden de riesgo:

| # | Hueco | Consecuencia |
|---|---|---|
| 1 | **El orden del reload de `config`** | si se agrega un módulo que lee `config` a nivel de módulo y no se suma a `_actualizar_config_y_recargar`, los parámetros del sidebar dejan de tener efecto **en silencio**. Ningún test lo cubre porque requiere sacar `ejecutar_pipeline` de `app.py`. |
| 2 | **Coherencia de unidades** | nada verifica que `CAPACIDAD_*` esté en unidades de volumen y `CAPACIDAD_EVACUACION_*` en tn/d. Cargar `25` en vez de `25000` estrangula la planta sin dar error. El bug del HALLAZGO-0 (doble multiplicación) es exactamente esto. |
| 3 | **`ejecutar_pipeline` de `app.py`** | no es importable sin ejecutar Streamlit, así que `conftest.py` reimplementa la secuencia. Esa duplicación puede desincronizarse. |
| 4 | **La UI** | ningún test toca `ui/`. Son ~2.400 líneas. |
| 5 | **`calcular_propiedades_gas`** | se verifica que agregue las columnas y que los valores sean plausibles, pero no los números contra una referencia. Haría falta un caso conocido del Excel. |
| 6 | **Regresión numérica contra el Excel** | no hay ningún test que compare los resultados del modelo Python contra los del Excel original. Es la validación que más valdría para cerrar la migración. |

---

## Cómo agregar una validación

1. **Elegí la capa.** ¿Es un problema del dato (1-3), de cómo se configuró la
   corrida (4), o del resultado (5)?
2. **Decidí si bloquea o avisa.** Bloquea solo si el resultado no es confiable.
   Ante la duda, avisá: un error que frena la corrida por algo corrible es peor
   que un aviso.
3. **Escribí el test negativo.** Datos rotos a propósito que hagan saltar el
   chequeo. Sin eso no sabés si funciona.
4. **Anotalo acá.**

Si el invariante todavía no se cumple con los datos reales, marcá el test
`xfail` con un `reason` que apunte al hallazgo. **No lo borres**: el xfail no
estricto avisa solo el día que se corrige.
