# Troubleshooting & Known Issues

## Known Issues

### KI-001 · Scraper bot-blocking (HTTP 403 / empty result)
**Symptom:** `run_scraper()` returns an empty list or fewer than 10 listings.  
**Cause:** Avito.ma applies rate-limiting and JS fingerprinting. Headless Chrome is detectable.  
**Fix:**
1. Increase `DELAY_MIN` / `DELAY_MAX` in `src/extract/scraper.py`.
2. Rotate user-agent strings.
3. Retry: `make run` uses exponential backoff (3 attempts, 10s base delay).

---

### KI-002 · `staging.raw_annonces` accumulates rows between failed runs
**Symptom:** Clean layer processes stale data from a previous aborted run.  
**Cause (old):** Staging was not filtered by run — all rows were read together.  
**Fix (FIX #15):** `run_staging()` now returns a `run_id` (UUID). `run_clean(run_id=...)` filters staging by that ID. Stale rows from previous runs are ignored until `make clean-db` is called.

---

### KI-003 · Sale listings scraped from rental URL
**Symptom:** Listings with prix > 500,000 DH appear in the rental dataset (e.g. 1,287,000 DH for 99m²).  
**Cause:** Avito sometimes surfaces "mabni lil bai3" (for-sale) listings on the rental search URL.  
**Fix (FIX #35):** `_apply_missing_value_strategy()` now drops `prix_type='mensuel'` rows with `prix > 150,000 DH` and logs a warning. If you are studying the property sale market, set `RENTAL_MAX_DH = float('inf')` to disable this filter.

---

### KI-004 · Arabic city names not normalised
**Symptom:** `ville` column contains `طنجة` or `مراكش` alongside `Tanger` / `Marrakech`, causing duplicate groups in BI.  
**Fix (FIX #32):** `_VILLE_MAP` in `clean_data.py` now includes the most common Arabic city name variants. Add new entries to the map if new Arabic names appear in the logs.

---

### KI-005 · GX bronze suite validates on empty DataFrame
**Symptom:** GX expectations always pass even when real data has issues.  
**Cause (old):** The suite was built from a `pd.DataFrame(columns=[...])` — no actual rows.  
**Fix (FIX #30):** `_build_bronze_suite()` now uses two realistic sample rows, giving GX real data types and non-null values to validate against.

---

### KI-006 · Connection pool exhaustion under high concurrency
**Symptom:** `PoolError: connection pool exhausted` in logs.  
**Cause:** Default pool max is 10. Parallel pipeline stages can exhaust this.  
**Fix:** Increase `maxconn` in `src/utils/db.py → _get_pool()`, or run pipeline stages sequentially (current default).

---

### KI-007 · `make migrate` fails with "relation does not exist"
**Symptom:** Migration `bi_001_add_region_label` fails because `bi_schema.dim_localisation` doesn't exist yet.  
**Cause:** Migrations run before DDL if `run_all_migrations()` is called standalone.  
**Fix:** Always run `make run` (full pipeline) first to create base tables, then individual migrations will apply correctly. The pipeline itself calls migrations after DDL creation.

---

## Common Errors

| Error | Likely Cause | Fix |
|---|---|---|
| `psycopg2.OperationalError: could not connect` | DB not running | `make docker-up` |
| `BronzeValidationError: too few records` | Scraper blocked | Wait and retry |
| `CleanValidationError: drop rate too high` | Scraper changed format | Check scraper selectors |
| `PoolError` | Pool exhausted | Increase `maxconn` in db.py |
| `GX checkpoint failed` | Data quality regression | Check Data Docs in `gx/` folder |
