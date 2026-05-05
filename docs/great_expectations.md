# Great Expectations — Setup & Usage Guide

## Architecture

```
src/
├── staging/
│   └── bronze_validator.py      ← Custom hard/soft rules (always runs)
├── clean/
│   └── clean_validator.py       ← Custom hard/soft rules (always runs)
└── expectations/
    ├── gx_bronze.py             ← GX suite for bronze layer (optional)
    └── gx_silver.py             ← GX suite for silver layer (optional)

gx/                              ← GX context (auto-created on first run)
├── expectations/
│   ├── bronze_suite.json        ← Stored expectation definitions
│   └── silver_suite.json
└── uncommitted/
    ├── validations/             ← Per-run JSON validation results
    └── data_docs/
        └── local_site/
            └── index.html       ← HTML report (open in browser)
```

## Why Two Layers?

| Layer | Purpose | Behaviour on failure |
|-------|---------|---------------------|
| `bronze_validator` | Fast, business-logic rules | **ABORTS** pipeline |
| `clean_validator` | Fast, transformation rules | **ABORTS** pipeline |
| GX bronze suite | Standardised, produces HTML report | **WARNS** (pipeline continues) |
| GX silver suite | Standardised, produces HTML report | **WARNS** (pipeline continues) |

GX is intentionally set to **warn-only** because the custom validators
already handle hard failures. GX adds auditability and human-readable reports.

## Installation

```bash
pip install great-expectations
```

Verify:
```bash
python -c "import great_expectations; print(great_expectations.__version__)"
```

## First Run

The GX context and expectation suites are **auto-created** when the pipeline
runs for the first time. No manual `gx init` needed.

```bash
# Run the full pipeline (GX runs automatically if installed)
python src/pipeline.py

# OR run GX manually on a specific bronze file
python -m src.expectations.gx_bronze data/bronze/avito_raw_20260502_104741.json

# OR run GX manually on the latest silver CSV
python -m src.expectations.gx_silver
```

## Viewing the HTML Report

After any run:
```bash
open gx/uncommitted/data_docs/local_site/index.html
# or on Linux:
xdg-open gx/uncommitted/data_docs/local_site/index.html
```

The report shows:
- ✅ / ❌ per expectation
- Run history (all previous validations)
- Data statistics per column

## Rebuilding the Suites

If you modify the expectations in `gx_bronze.py` or `gx_silver.py`,
force-rebuild the suite:

```python
from src.expectations.gx_bronze import rebuild_suite as rebuild_bronze
from src.expectations.gx_silver import rebuild_suite as rebuild_silver

rebuild_bronze()
rebuild_silver()
```

## Expectations Summary

### Bronze Suite (22 expectations)

| Category | Expectations |
|----------|-------------|
| Schema | 12 required columns exist |
| Completeness | prix ≥ 50%, ville ≥ 50%, titre ≥ 80%, scraped_at = 100% |
| Value validity | prix_type enum, surface 1–10 000, nb_chambres 1–20, nb_salles_bain 1–10 |
| Format | lien regex `avito.ma/`, scraped_at ISO 8601 |
| Uniqueness | lien unique per file |
| Table-level | row count 5–10 000, required columns present |

### Silver Suite (28 expectations)

| Category | Expectations |
|----------|-------------|
| Schema | 10 required output columns exist |
| Completeness | prix ≥ 99%, ville ≥ 99%, lien = 100%, prix_type = 100% |
| Numeric ranges | prix 100–500 000 DH, surface_m2 5–5 000, prix_par_m2 1–50 000, age_bien 0–200 |
| Categorical sets | prix_type, categorie_prix, region_label, is_grande_ville |
| Format | lien regex, scraped_at date format |
| Uniqueness | lien unique in silver |
| Table-level | row count ≥ 2, 'surface' raw column absent |

## Adding New Expectations

Edit `_build_bronze_suite()` or `_build_silver_suite()` in the relevant file,
then call `rebuild_suite()`. All standard GX expectations are available:

```python
# Examples
validator.expect_column_mean_to_be_between("prix", min_value=3000, max_value=20000)
validator.expect_column_pair_values_to_be_equal("prix_par_m2_check", "prix_par_m2")
validator.expect_column_quantile_values_to_be_between(
    "prix", quantile_ranges={"quantiles": [0.25, 0.75], "value_ranges": [[1000, 50000]]}
)
```

Full docs: https://docs.greatexpectations.io/docs/reference/expectations/
