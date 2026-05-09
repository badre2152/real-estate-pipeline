from src.config import PIPELINE_MAX_RETRIES as MAX_RETRIES, PIPELINE_RETRY_DELAY as RETRY_DELAY, MAX_PAGES  # noqa: E501
from src.utils.logger import get_logger
from src.utils.migrations import run_all_migrations
from src.utils.db import execute_query, close_pool
from src.warehouse.ml_schema import run_ml_schema
from src.warehouse.bi_schema import run_bi_schema
from src.clean.clean_data import run_clean
from src.staging.load_staging import run_staging
from src.extract.scraper import run_scraper
import sys
import time
import os
import random
from typing import Any, Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Great Expectations (optional) ───────────────────────────────────────
try:
    from src.expectations.gx_bronze import run_bronze_checkpoint
    from src.expectations.gx_silver import run_silver_checkpoint
    GX_ENABLED = True
except ImportError:
    GX_ENABLED = False

logger = get_logger("pipeline")


# ── Retry wrapper ───────────────────────────────────────────────────────

def _run(step_name: str,
         fn: Callable[...,
                      Any],
         *args: Any,
         **kwargs: Any) -> Any:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"[{step_name}] ── attempt {attempt}/{MAX_RETRIES}")
            result = fn(*args, **kwargs)
            logger.info(f"[{step_name}] ✓ success")
            return result
        except Exception as exc:
            logger.error(f"[{step_name}] ✗ attempt {attempt} failed: {exc}")
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY * \
                    (2 ** (attempt - 1)) + random.uniform(0, 2)
                logger.info(
                    f"[{step_name}] retrying in {delay:.1f}s… (exponential backoff)")
                time.sleep(delay)
            else:
                logger.critical(
                    f"[{step_name}] all {MAX_RETRIES} attempts failed — aborting."
                )
                raise


def _cleanup_staging() -> None:
    try:
        execute_query("TRUNCATE TABLE staging.raw_annonces RESTART IDENTITY;")
        logger.info("Staging table truncated.")
    except Exception as exc:
        logger.warning(f"Staging cleanup failed (non-fatal): {exc}")


# ── Main ────────────────────────────────────────────────────────────────

def run_pipeline() -> None:
    logger.info("━" * 55)
    logger.info("  AVITO.MA DATA PIPELINE — START")
    logger.info("━" * 55)
    t0 = time.time()

    try:
        # FIX #14: Apply all centralised DDL migrations once at pipeline start.
        _run("MIGRATIONS", run_all_migrations)

        # configured in src/config.py
        raw = _run("EXTRACT", run_scraper, max_pages=MAX_PAGES)

        # FIX #5-log: Log page count and raw record totals for traceability.
        _pages_used = MAX_PAGES
        _raw_count = len(raw) if raw else 0
        logger.info(
            f"[EXTRACT] pages_requested={_pages_used} | raw_records={_raw_count} | "
            f"valid_records={_raw_count} (after scraper validity guard)")

        if not raw:
            logger.critical(
                "EXTRACT returned an empty result set — "
                "possible bot block or source issue. Pipeline aborted."
            )
            sys.exit(1)

        if len(raw) < 10:
            logger.warning(
                f"EXTRACT returned only {len(raw)} listings "
                "(expected ≥ 10) — possible partial block."
            )

        # 2 — Staging (includes bronze_validator internally)
        # FIX #15: Capture run_id returned by run_staging for run isolation.
        run_id = _run("STAGING", run_staging, raw)

        # 2b — GX Bronze checkpoint (optional, runs after bronze_validator)
        if GX_ENABLED:
            import glob
            import os
            bronze_files = sorted(
                glob.glob(
                    os.path.join(
                        os.path.dirname(__file__),
                        "..",
                        "data",
                        "bronze",
                        "avito_raw_*.json")))
            if bronze_files:
                gx_passed = run_bronze_checkpoint(bronze_files[-1])
                if not gx_passed:
                    logger.warning(
                        "GX bronze checkpoint reported failures — "
                        "check Data Docs for details. Pipeline continues."
                    )
            else:
                logger.warning("GX bronze: no bronze file found to validate.")
        else:
            logger.info("GX not installed — skipping bronze checkpoint.")

        # 3 — Clean + Feature Engineering (includes clean_validator internally)
        # FIX #15: Pass run_id so clean layer only processes this run's data.
        df_clean = _run("CLEAN", run_clean, run_id)

        # 3b — GX Silver checkpoint (optional, runs after clean_validator)
        if df_clean is None or df_clean.empty:
            logger.critical("CLEAN returned no data — pipeline aborted.")
            sys.exit(1)

        if GX_ENABLED:
            gx_passed = run_silver_checkpoint(df_clean)
            if not gx_passed:
                logger.warning(
                    "GX silver checkpoint reported failures — "
                    "check Data Docs for details. Pipeline continues."
                )
        else:
            logger.info("GX not installed — skipping silver checkpoint.")

        # 4 — BI Schema (Star Schema → Power BI)
        _run("BI_SCHEMA", run_bi_schema, df_clean)

        # 5 — ML Schema (OBT → Feature Store)
        _run("ML_SCHEMA", run_ml_schema, df_clean)

        # 6 — Cleanup staging
        _cleanup_staging()

    except Exception as exc:
        logger.critical(f"Pipeline aborted: {exc}")
        sys.exit(1)

    # FIX #53: Return all connections to pool and shut it down cleanly.
    close_pool()

    elapsed = round(time.time() - t0, 1)
    logger.info("━" * 55)
    logger.info(f"  PIPELINE COMPLETE — {elapsed}s")
    logger.info("━" * 55)


if __name__ == "__main__":
    run_pipeline()
