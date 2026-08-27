#!/usr/bin/env python3
"""
Genera docs/mapa.md a partir del codigo, no de la memoria de nadie.

El problema de todo mapa de modulos escrito a mano es que miente en silencio a
las dos semanas. Este script lee el arbol real con `ast` y escribe el archivo
entero: docstrings de una linea, simbolos publicos, quien importa a quien y
—lo mas util— que modulos leen `config` a nivel de import (la lista que hay que
mantener sincronizada con `_actualizar_config_y_recargar`; ver decisiones/0004).

Uso
---
    python tools/mapa_modulos.py            # imprime por stdout
    python tools/mapa_modulos.py --escribir # (re)escribe docs/mapa.md
    python tools/mapa_modulos.py --check    # exit 1 si docs/mapa.md quedo viejo (CI)
"""

from __future__ import annotations

import argparse
import ast
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "docs" / "mapa.md"

IGNORAR = {".venv", "venv", "__pycache__", ".git", "node_modules",
           ".pytest_cache", "build", "dist", ".mypy_cache", "outputs"}


def modulos_del_proyecto() -> list[Path]:
    """Ordenados agrupando por carpeta: los archivos de un directorio van
    juntos, antes de sus subdirectorios, para que el arbol no repita
    encabezados."""
    modulos = [
        p for p in RAIZ.rglob("*.py")
        if not any(parte in IGNORAR for parte in p.parts)
    ]
    return sorted(modulos, key=lambda p: (str(p.parent), p.name))


def analizar(path: Path) -> dict:
    """Docstring de una linea, simbolos publicos e imports internos."""
    try:
        arbol = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as e:
        return {"error": str(e), "resumen": "", "publicos": [],
                "importa": set(), "lineas": 0}

    doc = ast.get_docstring(arbol) or ""
    resumen = doc.strip().split("\n")[0].strip().rstrip(".") if doc else ""

    publicos = [
        n.name for n in arbol.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not n.name.startswith("_")
    ]

    raices_locales = {
        p.name for p in RAIZ.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    } | {p.stem for p in RAIZ.glob("*.py")}

    importa: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                if alias.name.split(".")[0] in raices_locales:
                    importa.add(alias.name)
        elif isinstance(nodo, ast.ImportFrom) and not nodo.level:
            if nodo.module and nodo.module.split(".")[0] in raices_locales:
                importa.add(nodo.module)

    lineas = path.read_text(encoding="utf-8").count("\n") + 1
    return {"error": None, "resumen": resumen, "publicos": publicos,
            "importa": importa, "lineas": lineas}


def construir() -> str:
    modulos = modulos_del_proyecto()
    datos = {str(p.relative_to(RAIZ)): analizar(p) for p in modulos}

    lineas = [
        "# Mapa de módulos",
        "",
        f"<!-- GENERADO por tools/mapa_modulos.py el {date.today()} — no editar a mano. -->",
        "<!-- Regenerar: python tools/mapa_modulos.py --escribir -->",
        "",
        "El *qué significa* está en `dominio.md`; acá va el *dónde está*.",
        "",
        "## Árbol",
        "",
        "```",
    ]

    carpeta_actual = None
    for rel, info in datos.items():
        rel_path = Path(rel)
        carpeta = rel_path.parent
        if carpeta != carpeta_actual:
            carpeta_actual = carpeta
            if str(carpeta) != ".":
                sangria_dir = "  " * (len(carpeta.parts) - 1)
                lineas.append(f"{sangria_dir}{carpeta.name}/")

        if rel_path.name == "__init__.py" and not info["publicos"] and not info["resumen"]:
            continue

        sangria = "  " * (len(rel_path.parts) - 1)
        etiqueta = f"{sangria}{rel_path.name}"

        if info["error"]:
            nota = f"⚠️ no parsea: {info['error'][:40]}"
        elif info["resumen"]:
            nota = info["resumen"][:64]
        elif info["publicos"]:
            nota = ", ".join(info["publicos"][:4])
            if len(info["publicos"]) > 4:
                nota += f", +{len(info['publicos']) - 4}"
        elif info["lineas"] <= 1:
            nota = "(vacío)"
        else:
            nota = ""

        lineas.append(f"{etiqueta:<40}{('# ' + nota) if nota else ''}".rstrip())

    lineas += ["```", ""]

    # Lectores de config: la lista de decisiones/0004.
    lectores = sorted(
        r for r, i in datos.items()
        if any(m == "config" or m.startswith("config.") for m in i["importa"])
        and Path(r).name not in ("app.py", "main.py")
    )
    if lectores:
        lineas += [
            "## Módulos que importan `config`",
            "",
            "Si alguno lee constantes **a nivel de módulo**, tiene que estar en",
            "`_actualizar_config_y_recargar` de `app.py` o los parámetros del",
            "sidebar no lo afectan (ver `decisiones/0004`):",
            "",
        ]
        lineas += [f"- `{m}`" for m in lectores]
        lineas.append("")

    lineas += ["## Dependencias internas", "",
               "| Módulo | Importa |", "|---|---|"]
    for rel, info in datos.items():
        otros = sorted(m for m in info["importa"] if m != "config")
        if otros:
            lineas.append(
                f"| `{rel}` | {', '.join(f'`{m}`' for m in otros)} |")

    vacios = [r for r, i in datos.items()
              if i["lineas"] <= 1 and not i["error"]
              and Path(r).name != "__init__.py"]
    if vacios:
        lineas += ["",
                   "## Módulos vacíos (trabajo empezado sin terminar)", ""]
        lineas += [f"- `{m}`" for m in vacios]

    lineas.append("")
    return "\n".join(lineas)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--escribir", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    contenido = construir()

    if not (args.escribir or args.check):
        print(contenido)
        return 0

    if args.check:
        if not SALIDA.is_file():
            print(f"No existe {SALIDA}. Corré --escribir.", file=sys.stderr)
            return 1
        # Se compara sin la linea de fecha, que cambia sola.
        def sin_fecha(t):
            return "\n".join(l for l in t.splitlines()
                             if not l.startswith("<!-- GENERADO"))
        if sin_fecha(SALIDA.read_text(encoding="utf-8")) != sin_fecha(contenido):
            print("docs/mapa.md quedó viejo. Corré: "
                  "python tools/mapa_modulos.py --escribir", file=sys.stderr)
            return 1
        print("docs/mapa.md al día.")
        return 0

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(contenido, encoding="utf-8")
    print(f"{SALIDA.relative_to(RAIZ)} escrito.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
