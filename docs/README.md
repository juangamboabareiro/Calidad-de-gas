# Modelo de acondicionamiento de gas CN-VM

Migración a Python del modelo Excel de balance de gas y producción de LGN de la
cascada **TTY-TBX → TTY-Dew Point → MEGA**.

## Correr

```bash
pip install -r requirements.txt

streamlit run app.py     # la app (sidebar → subir inputs.xlsx → ▶️ Ejecutar)
python main.py           # el pipeline sin UI (usa config.PATH_INPUTS)
```

El Excel de inputs va en `datos/inputs.xlsx` (o el path de `config.PATH_INPUTS`).

## Tests

```bash
pip install -r tests/requirements-test.txt
pytest -m "not integracion"   # unitarios, no necesitan datos
pytest                        # todo (integración usa datos/inputs.xlsx)
```

## Dónde está cada cosa

| Quiero saber... | Voy a... |
|---|---|
| qué significan los números | `docs/dominio.md` |
| de qué celda del Excel sale un dato | `docs/linaje.md` |
| por qué algo está hecho así | `docs/decisiones/` |
| dónde está el código | `docs/mapa.md` (generado: `python tools/mapa_modulos.py`) |
| qué chequeos existen y qué falta validar | `docs/validaciones.md` |
| problemas conocidos de datos y config | `docs/HALLAZGOS.md` |
| notas de trabajo / borradores | `docs/notas.md` (scratchpad, no es fuente de verdad) |

Si vas a trabajar con un LLM o agente: `CLAUDE.md` es el punto de entrada.
