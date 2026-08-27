# CLAUDE.md

> Punto de entrada para cualquier LLM o agente. Corto a propósito: solo lo que
> hay que saber ANTES de tocar nada. El detalle está en los links.

## Qué es

App Streamlit + pipeline pandas que modelan el balance de gas y la producción
de LGN de la cascada **TTY-TBX → TTY-Dew Point → MEGA**. Entrada: un Excel de
14 hojas. Migración de un modelo que vivía en Excel.

## Leé primero, en este orden

1. `docs/dominio.md` — el modelo físico. Sin esto los números no significan nada.
2. `docs/HALLAZGOS.md` — problemas conocidos de datos y config. No los "redescubras".
3. `docs/decisiones/` — antes de cambiar cómo funciona algo.
4. `docs/linaje.md` — si vas a tocar inputs o loaders.
5. `docs/mapa.md` — dónde está el código (generado; regenerar si desconfiás).

`docs/notas.md` es un scratchpad con borradores viejos: NO es fuente de verdad.

## Reglas que no se negocian

1. **`vol_disponible = vol_asignado + vol_derivado + bypass`** por eslabón. Si
   un cambio rompe esto, el cambio está mal.
2. **La restricción activa es la evacuación de LGN (tn/d)**, no el ingreso.
3. **TBX→DP es traspaso (mismo gas), DP→MEGA es derivación (mezcla).** No
   unificarlos. Ver `decisiones/0003`.
4. **Cromatografía por `(Area, Gasoducto)` con fallback `Area+Sufijo`.** No
   volver al merge por `Area`. Ver `decisiones/0001`.
5. **Tres escalas de unidades conviven** (unidad de volumen, MMm³/d, tn/d).
   Chequear la escala de cada operando antes de escribir una fórmula
   (`dominio.md` §5).

## Trampas activas (no las repitas)

- **Hay DOS implementaciones de la cascada**: `modelar_TTY`/`modelar_MEGA`
  (usa `app.py`/`main.py`) y `registro`+`resolver_cascada` (usa el tab
  sandbox). Hoy dan idéntico; `TestEquivalenciaCascadas` es lo único que lo
  garantiza. Si tocás una, corré ese test. Ver HALLAZGO-6.
- `comunes["matriz_inyecciones"]` es la versión **cruda y ancha**, no la
  melteada de `inputs`. No reemplazar.
- La fila de una derivación necesita `fillna("derivacion")` en `Origen_tabla`
  o desaparece del panel de orígenes.
- `registro_base` filtra `Retenidos-RTP` por los strings literales `"TBX"`,
  `"Dew point"`, `"TBX MEGA"`. Renombrar en el Excel = planta con retención
  cero, sin error.
- Módulo nuevo que lea `config` a nivel de módulo → agregarlo a
  `_actualizar_config_y_recargar` de `app.py`, o el sidebar no lo afecta.
- `config.py` tiene un bloque duplicado con doble multiplicación
  (HALLAZGO-0). No copiar el patrón.

## Antes de dar un cambio por terminado

```bash
pytest -m "not integracion"       # verde sin excepción
pytest                            # si hay Excel a mano
python tools/mapa_modulos.py --check   # el mapa no quedó viejo
```

- ¿Cambiaste una regla del modelo? → test + entrada en `docs/decisiones/`.
- ¿Descubriste algo que contradice `dominio.md` o `linaje.md`? → corregilos en
  el mismo commit.
- Los 3 XFAIL de los tests son a propósito (HALLAZGOS 1 y 2). No los borres.
