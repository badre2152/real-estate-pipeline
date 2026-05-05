# Changelog

All notable changes to this project are documented here.

---

## [Unreleased]

### Planned
- Streamlit dashboard connected to BI schema views
- Apache Airflow DAG for scheduled daily runs
- Property type classification (`type_bien` field)
- Multi-city scraping with configurable city list
- Model training pipeline once dataset reaches 500+ rows

---

## [0.3.0] - 2026-05-04

### Fixed
- **CRITICAL** `gx_silver.py`: `NameError: run_id` → corrected to `run_label` (pipeline now completes past GX)
- **CRITICAL** Scraper: prix extraction logic was inverted — skipped `"mois"` texts which are exactly the monthly price format
- **CRITICAL** `gx_bronze.py` / `gx_silver.py`: suite `add_or_update` after failed `delete` caused "suite already exists" crash; added fallback `get()`
- `_get_listing_urls`: changed `OR` to `AND` for rental filter — was collecting non-rental URLs
- `_FIELDS` in `load_staging.py` now includes `prix_type` (was missing from QC report)
- `_rule_lien_uniqueness` in `bronze_validator.py`: replaced O(n²) `list.count()` with `collections.Counter` O(n)
- `fetch_all` in `db.py`: added `with conn:` for consistent transaction management
- `logger.propagate = False` in `logger.py`: prevented duplicate console messages
- `dim_caracteristiques` UNIQUE constraint now uses `COALESCE` sentinels to handle NULL values correctly
- BI schema views: log message now reflects actual creation success/failure
- `_fetch_staging`: deduplicates by `lien` at read time using `DISTINCT ON`
- Outlier filter: minimum 30 rows required before applying quantile filter (was 10, still too low)
- `_safe_int` double-apply in `ml_schema.py` removed
- Added prix minimum threshold of 200 DH to filter scraper extraction errors

### Added
- Arabic city name variants in `_VILLE_MAP` (طنجة→Tanger, مراكش→Marrakech, etc.)
- `_QUARTIER_NORMALIZE` map for common duplicate spellings (Centre Ville variants)
- `TROUBLESHOOTING.md` with known issues and setup guide
- Real `log-analysis.md` based on actual pipeline data
- `scikit-learn==1.4.2` added to `requirements.txt`
- `LICENSE.txt` content updated

---

## [0.2.0] - 2026-05-02

### Added
- PostgreSQL Data Warehouse schema (star schema design for BI, OBT for ML)
- Bronze → Staging validation (`bronze_validator.py`) with hard/soft rules
- Staging → Clean validation (`clean_validator.py`) with pre/post checkpoints
- Great Expectations integration (bronze + silver suites) — optional
- `prix_type` field (mensuel / journalier / inconnu) across all layers
- Artefact ville detection and filtering ("COURS ET FORMATIONS")
- `is_grande_ville` and `region_label` feature engineering
- Star schema views for Power BI (`v_annonces_full`, `v_prix_par_ville`)
- Unit tests for bronze and clean validators (unittest)
- Docker setup (Dockerfile + docker-compose.yml)

### Fixed
- `UNIQUE(lien)` constraint on `staging.raw_annonces` to prevent cross-run duplicates
- `ON CONFLICT DO NOTHING` in all INSERT statements
- `_smart_title` respects French prepositions (de, du, la, le...)
- `_safe_int` returns `None` instead of `-1` for missing values in BI schema

---

## [0.1.0] - 2026-05-01

### Added
- Selenium scraper for Avito.ma real estate listings
- Bronze JSON persistence with timestamp-based filenames
- Staging load to PostgreSQL (`staging.raw_annonces`)
- Pipeline orchestration with retry logic (`src/pipeline.py`)
- Centralized logging (`pipeline.log`)
- `.env` configuration for DB credentials
