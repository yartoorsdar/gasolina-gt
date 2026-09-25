# Procedencia del historial (`data/db/`)

Serie de combustibles = **precio promedio monitoreado en Ciudad Capital, modalidad autoservicio**
(MEM, Dirección General de Hidrocarburos). No se mezclan los "precios de referencia"
semanales (con subsidio may–jul 2026), que son otra serie.

| Archivo | Cobertura | Fuente | Verificación |
|---|---|---|---|
| `regular.csv` `superior.csv` `diesel.csv` | 2021-01-01 → 2026-03-17 diario | MEM `Precios-Promedio-Nacionales-Diarios-2026-1-1.xlsx` (copia en `data/inbox/historico/`), obtenido de [Internet Archive 2026-04-06](https://web.archive.org/web/20260406125332/https://mem.gob.gt/wp-content/uploads/2026/03/Precios-Promedio-Nacionales-Diarios-2026-1-1.xlsx) | Idéntico a la versión archivada 2026-01-22 (`...Diarios-2025-3.xlsx`, 5,553 valores) y a `WEB-PRECIOS-DIARIOS-2024` salvo 7 días de Regular (30-sep→6-oct-2024: 25.00 → 28.00, corrección del MEM) |
| idem | 2026-02-23 → 2026-03-23 semanal | MEM Informe ejecutivo 2026-03-23 (`data/raw/`), vía [Internet Archive](https://web.archive.org/web/20260406024156/https://mem.gob.gt/wp-content/uploads/2026/03/INFORME-EJECUTIVO-DE-PRECIOS-DE-LOS-COMBUSTIBLES-AREA-METROPOLITANA-2026-03-23.pdf) | Los 5 puntos que se solapan con el diario coinciden exacto |
| idem | 2026-08-17 → 2026-09-21 semanal | MEM Informe ejecutivo 2026-09-21 (`data/raw/`) | — |
| idem | 2026-09-15 → 2026-09-24 diario | Serie MEM cargada manualmente (commit 3f0d1f0) | 16 y 21-sep coinciden con el informe ejecutivo |
| idem | 2026-09-05 (1 punto) | Chapin TV (prioridad baja; lo pisa cualquier dato MEM) | Coherente con MEM 31-ago y 07-sep |
| idem (servicio completo) | 2026-03-19, 03-23, 09-16, 09-21 | MEM Informes ejecutivos 23-mar y 21-sep (tabla "Servicio Completo", parseada del PDF) | Coincide con Prensa Libre (16-sep: 45.68 / 43.57 / 50.36) |
| `observaciones.csv` / `consenso.csv` | desde 2026-09-25, diario | Consejo multifuente: notas vía Bing News (Prensa Libre, Publinews, Emisoras Unidas, La Hora…) + GlobalPetrolPrices (= servicio completo) | Cada observación guarda URL y cita textual; precisión por medio medida contra el MEM |
| `mensual.csv` | 2020-01 → 2026-07 mensual | MEM `PRECIOS-PROMEDIO-MENSUAL-CONSUMIDOR-FINAL-EN-CIUDAD-CAPITAL-2026-07.pdf` (`data/raw/`, imagen transcrita; columnas autoservicio Superior sin aditivos, Regular, Diésel) | 186 meses contrastados con el diario: diferencia media Q0.20. Anomalías del propio PDF: 2022-11 Superior = Regular (34.90); 2025-07 autoservicio = servicio completo |
| `anual_semilla.csv` | 2002 → 2019 anual | `consolidado.json` congelado previo (procedencia original desconocida) | Sin verificar — reemplazar si aparece fuente oficial |
| `wti.csv` | 2024-10-23 → hoy (días de mercado) | OilPriceAPI `/v1/prices/historical?period=past_year&interval=daily` (promedio diario) + `/latest` para hoy | Contrastado con [Trading Economics](https://tradingeconomics.com/commodity/crude-oil) (24-sep-2026 ≈ 94.76 cierre vs 93.30 promedio) |

## Huecos conocidos (sin dato diario/semanal oficial accesible)
- **2026-03-24 → 2026-08-16**: mem.gob.gt bloquea descargas automáticas (Cloudflare) y el Internet
  Archive no guardó los informes de esas semanas. Cubierto solo por promedios mensuales (abr–jul) en
  `mensual.csv`. Para llenarlo: descargar a mano los "Informe ejecutivo de precios" semanales o el
  `Precios-Promedio-Nacionales-Diarios-2026` más reciente en `data/raw/` / `data/inbox/historico/`.

## Prioridad anual (`main._promedios_anuales`)
diario (≥300 días) > mensual oficial > semilla > diario parcial.
