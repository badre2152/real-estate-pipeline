# Pipeline Log Analysis — Real Data

> Generated from 6 bronze files collected on 2026-05-02 and 2026-05-03.
> Total: 208 records across 11 pipeline runs.

---

## Pipeline Run Summary

| Metric | Value |
|--------|-------|
| Total runs | 11 |
| Successful completions (full pipeline) | 0 |
| Abort reason | Great Expectations `NameError` / suite conflict |
| Stages that ran successfully | Extract, Staging |
| Stages never reached | Clean, BI Schema, ML Schema |

**Root cause:** `gx_silver.py` contained `run_id` (undefined) instead of `run_label`.
This has been fixed in the current version.

---

## Bronze Field Completeness (averaged across all runs)

| Field | Fill Rate | Status |
|-------|-----------|--------|
| prix | 100% | ✅ |
| ville | 100% | ✅ |
| quartier | 100% | ✅ |
| prix_type | 100% | ✅ |
| surface | ~95% | ✅ |
| nb_chambres | ~92% | ✅ |
| nb_salles_bain | ~92% | ✅ |
| etage | ~90% | ✅ |
| **annee_construction** | **0%** | ❌ |

### Key Finding: `annee_construction` = 0% in every run
The scraper cannot locate this field in the Avito listing HTML. The CSS selector
`elif "année" in label or "construction" in label` never matches because the page
renders this attribute differently. This is a known scraper limitation.

---

## Data Quality Issues Found in Bronze

| Issue | Count | Notes |
|-------|-------|-------|
| Artefact ville "COURS ET FORMATIONS" | 9/208 (4.3%) | Scraper captures non-property listings |
| Price 25 DH (Rabat, 240m²) | 1 | Scraper extracted wrong element |
| Price 499 DH (Marrakech, 60m²) | 1 | Suspiciously low — possible scraper error |
| Price 1,287,000 DH (Marrakech, 99m²) | 1 | Likely a sale listing, not rental |
| Arabic city names (طنجة, مراكش) | 2 | Normalized to French in clean layer |
| Cross-run duplicate liens | 6 liens duplicated | Handled by `ON CONFLICT DO NOTHING` |

---

## Geographic Distribution

| City | Records | % |
|------|---------|---|
| Casablanca | 89 | 43% |
| Marrakech | 34 | 16% |
| Rabat | 26 | 12% |
| Tanger | 14 | 7% |
| Temara | 7 | 3% |
| Agadir | 6 | 3% |
| Other | 32 | 15% |

**Note:** Casablanca dominates due to `MAX_PAGES=1`. Increasing to 3-5 pages
would improve geographic diversity. 9 major Moroccan cities are absent entirely.

---

## Prix Type Distribution

All 208 records have `prix_type = "mensuel"`. No daily rentals were detected.
This is expected for the `immobilier-à_louer` category on Avito.

---

## Surface Distribution

| Metric | Value |
|--------|-------|
| Min | 0 m² (2 records — scraper error) |
| Median | 80 m² |
| Max | 1311 m² |

Records with `surface = 0` will be filtered out in the clean layer.
