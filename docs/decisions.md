# Decisions Log — [Nombre del modelo]

> Registro corto de decisiones de diseño no obvias tomadas durante la
> migración. Objetivo: que en 6 meses nadie (ni vos mismo) tenga que
> re-derivar por qué algo se hizo de determinada manera.
> Formato: fecha, decisión, alternativa descartada, motivo.

---

### #1 — [2026-07-15] Reshape de sheet de cromatografía
- **Decisión:** usar `pd.melt` para pasar de formato wide (una columna por
  componente) a long (una fila por componente-locación-fecha).
- **Alternativa descartada:** loop manual componente por componente.
- **Motivo:** más robusto a que cambie la cantidad de componentes en el
  futuro; evita hardcodear nombres de columnas.

### #2 — [fecha] Manejo de celdas vacías en `[sheet]`
- **Decisión:**
- **Alternativa descartada:**
- **Motivo:**

### #3 — [fecha] Fórmula de conversión de unidades en `caudal_m3d`
- **Decisión:** replicar exactamente la fórmula del Excel (`=Sheet2!C4*24`)
  en vez de usar una constante de conversión "más correcta" físicamente.
- **Motivo:** mantener consistencia con reportes históricos ya emitidos
  con el Excel viejo; documentar la discrepancia si la hay en
  `data_dictionary.md`.

### #4 — [fecha] Macro/lógica no determinística encontrada en el Excel
- **Decisión:**
- **Motivo:**

---

## Pendientes de decisión
- [ ] Ítem que todavía no se resolvió y hay que definir antes de cerrar
  la migración.
