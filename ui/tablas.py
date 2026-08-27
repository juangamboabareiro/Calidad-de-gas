"""
Explorador de tablas + comparador contra el Excel de referencia.

Reemplaza el contenido del tab "Tablas" de app.py por una sola llamada:

    from ui.tablas import panel_tablas
    ...
    with tab_tablas:
        panel_tablas(resultados)

`registro_tablas()` recorre el dict de resultados y junta TODO lo que sea
DataFrame / Series / dict de escalares, así que cualquier cosa que se agregue
al return de `ejecutar_pipeline()` en el futuro aparece sola en el selector,
sin tocar este archivo.
"""

import io
import re
import unicodedata

import pandas as pd
import streamlit as st


# ===========================================================================
# Helpers
# ===========================================================================

def _slug(texto) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(texto).lower()).strip("_")


def _norm_col(texto) -> str:
    """Normaliza un nombre de columna para poder aparearlo con el del Excel:
    sin tildes, sin espacios ni guiones, minúsculas."""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _fmt(valor, decimales=1, unidad=""):
    if valor is None:
        return "—"
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if pd.isna(f):
        return "—"
    return f"{f:,.{decimales}f}{unidad}"


def _a_dataframe(obj, nombre_valor="Valor", nombre_indice="Compuesto"):
    """Series / DataFrame / dict de escalares -> DataFrame presentable, sin
    asumir la forma exacta que devuelve cada función de dominio."""
    if isinstance(obj, pd.DataFrame):
        df = obj.copy()
        if not isinstance(df.index, pd.RangeIndex):
            df = df.reset_index()
        return df
    if isinstance(obj, pd.Series):
        df = obj.to_frame(name=nombre_valor)
        df.index.name = df.index.name or nombre_indice
        return df.reset_index()
    if isinstance(obj, dict):
        return pd.DataFrame([obj])
    return pd.DataFrame()


def _descargar_csv(df: pd.DataFrame, nombre: str, key: str, label=None):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button(
        label or f"Descargar {nombre}.csv",
        data=buf.getvalue(),
        file_name=f"{nombre}.csv",
        mime="text/csv",
        key=key,
    )


# ===========================================================================
# Registro de tablas
# ===========================================================================

def registro_tablas(resultados: dict, prefijo: str = "") -> dict:
    """Nombre -> DataFrame de todo lo tabular que haya en el dict de resultados.

    Recorre recursivamente: dicts de DataFrames (como `resultados['tablas']`),
    dicts anidados (como `resultados['plantas'][planta]['tabla_total']`) y dicts
    de escalares (como `...['flujos']`, que se muestra como fila única).
    """
    reg = {}

    for clave, valor in resultados.items():
        nombre = f"{prefijo}{clave}"

        if isinstance(valor, (pd.DataFrame, pd.Series)):
            reg[nombre] = _a_dataframe(valor, nombre_valor=str(clave))

        elif isinstance(valor, dict):
            if valor and all(
                not isinstance(v, (pd.DataFrame, pd.Series, dict, list))
                for v in valor.values()
            ):
                # dict de escalares (flujos, params): una fila
                reg[nombre] = pd.DataFrame([valor])
            else:
                reg.update(registro_tablas(valor, prefijo=f"{nombre} · "))

    return {
        k: v for k, v in reg.items()
        if isinstance(v, pd.DataFrame) and not v.empty
        
    }


# ===========================================================================
# Filtros
# ===========================================================================

def _filtrar(df: pd.DataFrame, key: str) -> pd.DataFrame:
    if df.empty:
        return df

    c1, c2 = st.columns([2, 1])
    with c1:
        cols = st.multiselect(
            "Columnas", list(df.columns), default=list(df.columns), key=f"cols_{key}"
        )
    with c2:
        busqueda = st.text_input(
            "Buscar texto (en todas las columnas)", key=f"busq_{key}",
            placeholder="ej: fortin, aguada",
        )

    out = df[cols] if cols else df

    if busqueda.strip():
        patron = busqueda.strip()
        mascara = out.apply(
            lambda s: s.astype(str).str.contains(patron, case=False, na=False, regex=False)
        ).any(axis=1)
        out = out[mascara]

    cat_cols = [c for c in out.columns if not pd.api.types.is_numeric_dtype(out[c])]
    num_cols = [c for c in out.columns if pd.api.types.is_numeric_dtype(out[c])]

    f1, f2 = st.columns(2)
    with f1:
        if cat_cols:
            col_cat = st.selectbox("Filtrar por valores de", ["—"] + cat_cols, key=f"cat_{key}")
            if col_cat != "—":
                valores = sorted(out[col_cat].dropna().astype(str).unique().tolist())
                elegidos = st.multiselect(
                    f"Valores de {col_cat}", valores, default=valores, key=f"catval_{key}"
                )
                if elegidos:
                    out = out[out[col_cat].astype(str).isin(elegidos)]
    with f2:
        if num_cols:
            col_num = st.selectbox("Filtrar por rango de", ["—"] + num_cols, key=f"num_{key}")
            if col_num != "—":
                serie = pd.to_numeric(out[col_num], errors="coerce")
                if serie.notna().any():
                    lo, hi = float(serie.min()), float(serie.max())
                    if lo < hi:
                        rango = st.slider(
                            f"Rango de {col_num}", lo, hi, (lo, hi), key=f"rango_{key}"
                        )
                        out = out[serie.between(*rango) | serie.isna()]

    return out


def _vista_tabla(nombre: str, df: pd.DataFrame, key: str):
    filtrada = _filtrar(df, key)

    o1, o2, o3, o4 = st.columns([1, 1, 1, 2])
    with o1:
        decimales = st.number_input("Decimales", 0, 8, 3, key=f"dec_{key}")
    with o2:
        transponer = st.toggle("Transponer", value=False, key=f"tr_{key}")
    with o3:
        totales = st.toggle("Fila de totales", value=True, key=f"tot_{key}")
    with o4:
        ordenar = st.selectbox("Ordenar por", ["—"] + list(filtrada.columns), key=f"ord_{key}")

    num_cols = [c for c in filtrada.columns if pd.api.types.is_numeric_dtype(filtrada[c])]

    vista = filtrada.copy()
    if ordenar != "—":
        vista = vista.sort_values(ordenar, ascending=False, na_position="last")

    if totales and num_cols:
        fila = {c: (vista[c].sum() if c in num_cols else "") for c in vista.columns}
        primera = vista.columns[0]
        if primera not in num_cols:
            fila[primera] = "TOTAL"
        vista = pd.concat([vista, pd.DataFrame([fila])], ignore_index=True)

    st.caption(
        f"{len(filtrada):,} filas × {len(filtrada.columns)} columnas "
        f"(de {len(df):,} × {len(df.columns)} sin filtrar)"
    )

    if transponer:
        st.dataframe(vista.T, use_container_width=True, height=460)
    else:
        st.dataframe(
            vista.style.format(precision=int(decimales), thousands=","),
            use_container_width=True, height=460,
        )

    _descargar_csv(filtrada, _slug(nombre), key=f"dl_{key}")


# ===========================================================================
# Comparador contra el Excel de referencia
# ===========================================================================

def comparar_con_excel(df_calc: pd.DataFrame, nombre_tabla: str, key: str):
    st.markdown("#### Comparar contra el Excel de referencia")
    st.caption(
        "Subí el Excel original, elegí la hoja y la clave de apareo. "
        "Se comparan las columnas numéricas que existan en las dos tablas "
        "(el nombre se aparea sin tildes, espacios ni mayúsculas)."
    )

    ref_file = st.file_uploader(
        "Excel de referencia (.xlsx)", type=["xlsx", "xlsm"], key=f"ref_{key}"
    )
    if ref_file is None:
        return

    try:
        xls = pd.ExcelFile(ref_file)
    except Exception as e:
        st.error(f"No se pudo abrir el archivo: {e}")
        return

    h1, h2, h3 = st.columns([2, 1, 1])
    with h1:
        hoja = st.selectbox("Hoja", xls.sheet_names, key=f"hoja_{key}")
    with h2:
        header = st.number_input("Fila de encabezado (0 = primera)", 0, 50, 0, key=f"hdr_{key}")
    with h3:
        usecols = st.text_input(
            "Rango de columnas (opcional)", "", key=f"uc_{key}", placeholder="ej: B:AC"
        )

    try:
        df_ref = pd.read_excel(
            xls, sheet_name=hoja, header=int(header), usecols=usecols.strip() or None
        )
    except Exception as e:
        st.error(f"No se pudo leer la hoja: {e}")
        return

    df_ref = df_ref.dropna(axis=1, how="all").dropna(axis=0, how="all")
    with st.expander(f"Ver hoja '{hoja}' cruda ({len(df_ref):,} filas)"):
        st.dataframe(df_ref, use_container_width=True, height=280)

    k1, k2 = st.columns(2)
    with k1:
        clave_calc = st.selectbox(
            "Clave en la tabla calculada", ["(por posición)"] + list(df_calc.columns),
            key=f"kc_{key}",
        )
    with k2:
        clave_ref = st.selectbox(
            "Clave en el Excel", ["(por posición)"] + list(df_ref.columns), key=f"kr_{key}"
        )

    mapa_calc = {_norm_col(c): c for c in df_calc.columns}
    mapa_ref = {_norm_col(c): c for c in df_ref.columns}
    comunes = [
        k for k in mapa_calc
        if k in mapa_ref
        and pd.api.types.is_numeric_dtype(df_calc[mapa_calc[k]])
        and pd.api.types.is_numeric_dtype(df_ref[mapa_ref[k]])
    ]
    if not comunes:
        st.warning(
            "No hay columnas numéricas con nombre equivalente en las dos tablas. "
            "Revisá la fila de encabezado o el rango de columnas."
        )
        return

    elegidas = st.multiselect(
        "Columnas a comparar",
        [mapa_calc[k] for k in comunes],
        default=[mapa_calc[k] for k in comunes],
        key=f"cmp_{key}",
    )
    if not elegidas:
        return

    t1, t2, t3 = st.columns(3)
    with t1:
        tol_rel = st.number_input("Tolerancia relativa (%)", 0.0, 100.0, 0.5, 0.1, key=f"tolr_{key}")
    with t2:
        tol_abs = st.number_input("Tolerancia absoluta", 0.0, 1e9, 0.0, key=f"tola_{key}")
    with t3:
        solo_dif = st.toggle("Solo diferencias", value=True, key=f"sd_{key}")

    # --- apareo calculado / referencia ---
    if clave_calc != "(por posición)" and clave_ref != "(por posición)":
        izq = df_calc[[clave_calc] + elegidas].copy()
        izq[clave_calc] = izq[clave_calc].astype(str).str.strip().str.lower()

        der_cols = [mapa_ref[_norm_col(c)] for c in elegidas]
        der = df_ref[[clave_ref] + der_cols].copy()
        der[clave_ref] = der[clave_ref].astype(str).str.strip().str.lower()
        der = der.rename(columns={clave_ref: clave_calc})
        der = der.rename(columns={mapa_ref[_norm_col(c)]: f"{c}__ref" for c in elegidas})

        par = izq.merge(der, on=clave_calc, how="outer")
        etiqueta_clave = clave_calc
    else:
        n = min(len(df_calc), len(df_ref))
        izq = df_calc[elegidas].head(n).reset_index(drop=True)
        der = df_ref[[mapa_ref[_norm_col(c)] for c in elegidas]].head(n).reset_index(drop=True)
        der.columns = [f"{c}__ref" for c in elegidas]
        par = pd.concat([izq, der], axis=1)
        par.insert(0, "fila", range(1, n + 1))
        etiqueta_clave = "fila"
        if len(df_calc) != len(df_ref):
            st.info(
                f"Distinta cantidad de filas: calculada {len(df_calc):,} vs "
                f"Excel {len(df_ref):,}. Se comparan las primeras {n:,}."
            )

    # --- diferencias en formato largo: una fila por (clave, columna) ---
    piezas = []
    for c in elegidas:
        sub = pd.DataFrame({
            etiqueta_clave: par[etiqueta_clave],
            "columna": c,
            "calculado": pd.to_numeric(par[c], errors="coerce"),
            "excel": pd.to_numeric(par[f"{c}__ref"], errors="coerce"),
        })
        sub["diferencia"] = sub["calculado"] - sub["excel"]

        # Con excel == 0 el error relativo no existe: ahí manda solo la
        # tolerancia absoluta. Si se rellenara dif_% con 0, esas celdas pasarían
        # siempre, y es justo donde se esconden los desvíos grandes.
        base = sub["excel"].abs().mask(lambda s: s == 0)
        sub["dif_%"] = sub["diferencia"].abs() / base * 100

        ambos_vacios = sub["calculado"].isna() & sub["excel"].isna()
        ok_abs = (sub["diferencia"].abs() <= tol_abs).fillna(False)
        ok_rel = (sub["dif_%"] <= tol_rel).fillna(False)
        sub["ok"] = ok_abs | ok_rel | ambos_vacios

        piezas.append(sub)

    dif = pd.concat(piezas, ignore_index=True)
    fuera = dif[~dif["ok"]]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Celdas comparadas", f"{len(dif):,}")
    m2.metric("Fuera de tolerancia", f"{len(fuera):,}")
    m3.metric("Máx. diferencia abs.", _fmt(dif["diferencia"].abs().max(), 4))
    m4.metric(
        "Máx. diferencia %",
        _fmt(dif["dif_%"].max(), 2, "%") if dif["dif_%"].notna().any() else "—",
    )

    if len(fuera) == 0:
        st.success(f"Todo dentro de tolerancia ({tol_rel}% / {tol_abs}).")
    else:
        st.warning(f"{len(fuera):,} celdas fuera de tolerancia.")
        peores = (
            fuera.groupby("columna")
            .agg(celdas=("ok", "size"),
                 max_dif_pct=("dif_%", "max"),
                 max_dif_abs=("diferencia", lambda s: s.abs().max()))
            .sort_values("celdas", ascending=False)
            .reset_index()
        )
        st.markdown("**Columnas con más diferencias**")
        st.dataframe(peores, use_container_width=True)

    mostrar = (fuera if solo_dif else dif).sort_values(
        "dif_%", ascending=False, na_position="last"
    )
    st.dataframe(
        mostrar.style.format({
            "calculado": "{:,.4f}", "excel": "{:,.4f}",
            "diferencia": "{:,.4f}", "dif_%": "{:,.2f}",
        }),
        use_container_width=True, height=420,
    )
    _descargar_csv(
        dif, f"diff_{_slug(nombre_tabla)}", key=f"dldiff_{key}",
        label="Descargar comparación completa",
    )


# ===========================================================================
# Panel completo (esto es lo único que llama app.py)
# ===========================================================================

def panel_tablas(resultados: dict):
    """Explorador de todas las tablas + comparador contra el Excel."""
    registro = registro_tablas(resultados)

    if not registro:
        st.info("El pipeline no devolvió tablas para mostrar.")
        return

    st.markdown("#### Explorador de tablas")
    izq, der = st.columns([3, 1])
    with izq:
        nombre_tabla = st.selectbox("Tabla", list(registro.keys()), key="sel_tabla")
    with der:
        st.metric("Tablas disponibles", len(registro))

    df_sel = registro[nombre_tabla]
    _vista_tabla(nombre_tabla, df_sel, key=_slug(nombre_tabla))

    st.divider()
    comparar_con_excel(df_sel, nombre_tabla, key=_slug(nombre_tabla))

    st.divider()
    with st.expander("Ver dos tablas lado a lado"):
        s1, s2 = st.columns(2)
        nombres = list(registro.keys())
        with s1:
            n_a = st.selectbox("Izquierda", nombres, key="lado_a")
            st.dataframe(registro[n_a], use_container_width=True, height=420)
        with s2:
            n_b = st.selectbox(
                "Derecha", nombres, index=min(1, len(nombres) - 1), key="lado_b"
            )
            st.dataframe(registro[n_b], use_container_width=True, height=420)
