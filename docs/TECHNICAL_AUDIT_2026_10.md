# Real Estate Pipeline: Technical Audit

Audit basis: GitHub `master` on 2026-10-09. Source inspection and CI-backed PostgreSQL integration tests, not a production validation. Fixes are on `audit-pipeline-reliability-2026-10`.

## Scope inspected

Repository tree; `src/pipeline.py`, `src/utils/db.py`, `src/staging/load_staging.py`, `src/clean/clean_data.py`, `src/extract/scraper.py`, `src/warehouse/bi_schema.py`, `src/warehouse/ml_schema.py`, `src/utils/migrations.py`, `src/config.py`, `docker-compose.yml`, and existing tests.

## Findings

| Severity | File / evidence | Impact | Disposition |
| --- | --- | --- | --- |
| High | `src/pipeline.py`: failure path calls `sys.exit(1)` before `close_pool()` originally located after the try/except | Database connections may not be released on failed runs | **Fixed**: `close_pool()` moved into `finally`. Added regression test `src/tests/test_pipeline_cleanup.py` |
| High | `src/pipeline.py` `_cleanup_staging()` truncates `staging.raw_annonces` after success, while `src/extract/scraper.py` `_get_known_liens()` reads the same table as its incremental history | Successful runs erase the stored links used to recognize previously scraped listings | **Fixed**: scraper now reads previously cleaned links from `clean.annonces`, which persists across staging truncation. Added regression test |
| Medium | `src/staging/load_staging.py` `_INSERT` updates only run_id, prix, prix_type, scraped_at and loaded_at on `lien` conflicts | A changed listing can retain stale title, city, neighborhood, surface and other fields in staging | **Fixed in source**: staging refreshes all persisted attributes; SQL contract tests passed; live PostgreSQL coverage is partial |
| Medium | `src/clean/clean_data.py` `_INSERT` updates only a subset of fields on `lien` conflict | Existing cleaned listings can keep stale city, title, neighborhood, timestamps and derived fields | **Fixed in source**: clean upsert refreshes all persisted attributes; SQL contract tests passed; live PostgreSQL coverage is partial |
| Medium | `src/warehouse/bi_schema.py`: fact insert uses `ON CONFLICT (lien) DO NOTHING` | BI fact records will not reflect later updates to existing listings | **Fixed in source** under current-state model: BI fact upsert refreshes values and dimension keys; PostgreSQL integration upsert tests passed |
| Medium | `src/warehouse/ml_schema.py`: feature-store insert uses `ON CONFLICT (lien) DO NOTHING` | ML feature-store rows can become stale across runs | **Fixed in source** under current-state model: feature-store upsert refreshes values; PostgreSQL integration upsert tests passed |
| High | `src/clean/clean_data.py` `_load_to_db`: missing required columns logged an error then returned normally | Pipeline could report CLEAN success despite skipping its database write | **Fixed in source**: now raises `ValueError` before insert; added `test_clean_load_required_columns.py` (executed in CI) |
| Low | `src/tests/conftest.py` instructs `pytest tests/`, while tests are stored under `src/tests/` | Developer documentation could point to the wrong directory | Open: documentation cleanup |
| Informational | `.github/workflows/ci.yml` absent on `master`; `pipeline.yml` remains | The original audit lacked this checks workflow; the audit branch now adds Python checks | A Python checks workflow was added on the audit branch; it uses PostgreSQL 16 for integration tests |

## Fix verification

- Read-back of `src/pipeline.py` on the audit branch shows one `close_pool()` call in a `finally` clause covering the pipeline body, including `SystemExit`.
- Added a focused regression test for migration-step failure and connection-pool closure.
- Added SQL-contract regression checks for staging, clean, BI and ML upserts; these do not replace a database-backed integration test.
- Read-back of `src/extract/scraper.py` shows incremental history reads `clean.annonces` rather than truncated staging. Added a focused regression test.
- Required clean columns now raise rather than silently returning. Added focused regression coverage.
- GitHub Actions Run #27 passed Python compilation, pytest and PostgreSQL-backed integration tests on commit `622407e`.
- The suite includes migration rollback, dimension upsert, BI and ML listing updates, and BI transaction rollback.
- Docker image build, live scrape and end-to-end production execution have not been verified.
- No merge or production deployment performed.

## Next remediation batch

1. Confirm current-state reporting is intended; these changes refresh existing listings rather than retaining immutable snapshots.
2. Verify the latest CI result after documentation changes and review remaining data-flow edge cases.
3. Review the complete pull-request diff and merge only when all checks are satisfied.
