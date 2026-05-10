"""
Staging layer — loads raw scraped records into staging.raw_annonces.
Data is stored as-is (all TEXT); no transformation happens here.

FIX #15: Added run_id column to staging table.
         _fetch_staging in clean_data.py can filter by run_id to avoid
         mixing records from different pipeline runs when staging is not
         truncated between retries.
FIX #14: Inline DDL_UNIQUE_LIEN removed — now managed by utils/migrations.py.
"""

import json
import os
import uuid
from src.utils.db import execute_query, bulk_insert
from src.utils.logger import get_logger
from src.staging.bronze_validator import validate_bronze, BronzeValidationError

logger = get_logger("staging")

BRONZE_DIR = os.path.join(os.path.dirname(__file__), "../../data/bronze")

_DDL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS staging;"

# FIX #15: run_id column added so clean_data.py can filter by run.
_DDL_TABLE = """
CREATE TABLE IF NOT EXISTS staging.raw_annonces (
    id                  SERIAL PRIMARY KEY,
    run_id              TEXT NOT NULL DEFAULT 'legacy',
    titre               TEXT,
    prix                TEXT,
    prix_type           TEXT DEFAULT 'mensuel',
    ville               TEXT,
    quartier            TEXT,
    surface             TEXT,
    nb_chambres         TEXT,
    nb_salles_bain      TEXT,
    etage               TEXT,
    lien                TEXT,
    scraped_at          TEXT,
    loaded_at           TIMESTAMP DEFAULT NOW()
);
"""

# FIX #15: Migration to add run_id to existing tables without run_id.
_DDL_ADD_RUN_ID = """
ALTER TABLE staging.raw_annonces ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT 'legacy';
"""

# FIX: ON CONFLICT DO UPDATE (not DO NOTHING) — required for run_id isolation correctness.
# If the same lien was scraped in a previous run, we update run_id to the current run
# so _fetch_staging(run_id=current_run) can find it. DO NOTHING would silently drop it,
# causing _fetch_staging to return 0 rows for that lien in the current run.
_INSERT = """
INSERT INTO staging.raw_annonces
    (run_id, titre, prix, prix_type, ville, quartier, surface, nb_chambres,
     nb_salles_bain, etage, lien, scraped_at)
VALUES %s
ON CONFLICT (lien) DO UPDATE SET
    run_id     = EXCLUDED.run_id,
    prix       = EXCLUDED.prix,
    prix_type  = EXCLUDED.prix_type,
    scraped_at = EXCLUDED.scraped_at,
    loaded_at  = NOW()
"""

_FIELDS = [
    "titre", "prix", "prix_type", "ville", "quartier", "surface",
    "nb_chambres", "nb_salles_bain", "etage", "annee_construction",
]


def _latest_bronze_file() -> str | None:
    if not os.path.exists(BRONZE_DIR):
        return None
    files = []
    for root, dirs, filenames in os.walk(BRONZE_DIR):
        for f in filenames:
            if f.endswith(".json"):
                files.append(os.path.join(root, f))
    files = sorted(files, reverse=True)
    return files[0] if files else None


def _qc_report(records: list[dict]) -> None:
    """Log a fill-rate report for every field — initial quality control."""
    n = len(records)
    if n == 0:
        logger.warning("QC Report: 0 records — nothing to analyse.")
        return

    lines = [f"\n📋 STAGING QC REPORT — {n} records"]
    for field in _FIELDS:
        filled = sum(
            1 for r in records if r.get(field) and str(
                r[field]).strip())
        missing = n - filled
        pct = 100 * filled // n
        status = "✅" if pct >= 80 else ("⚠️" if pct >= 40 else "❌")
        lines.append(
            f"  {status} {field:<22}: {filled}/{n} filled ({pct}%) — {missing} missing"
        )

    daily = sum(1 for r in records if r.get("prix_type") == "journalier")
    monthly = sum(1 for r in records if r.get("prix_type") == "mensuel")
    unknown = n - daily - monthly
    lines.append(
        f"\n  📅 Prix type: {monthly} mensuel | {daily} journalier | {unknown} inconnu")

    logger.info("\n".join(lines))


def run_staging(
        records: list[dict] | None = None,
        run_id: str | None = None) -> str:
    """
    Load records into staging.raw_annonces.

    Parameters
    ----------
    records : list[dict] | None
        Raw scraped records. If None, reads latest bronze file.
    run_id : str | None
        Unique identifier for this pipeline run.
        Auto-generated (UUID4) if not provided.
        Returned so the caller can pass it to run_clean().

    Returns
    -------
    str
        The run_id used for this staging load.
    """
    # FIX #15: Generate a stable run_id for this invocation.
    if run_id is None:
        run_id = str(uuid.uuid4())

    logger.info(f"=== Staging load started (run_id={run_id}) ===")

    execute_query(_DDL_SCHEMA)
    execute_query(_DDL_TABLE)

    # Add run_id column if table already existed without it.
    try:
        execute_query(_DDL_ADD_RUN_ID)
    except Exception as e:
        logger.debug(
            f"run_id column already present or migration skipped: {e}")

    logger.info("staging.raw_annonces — schema/table ready.")

    if records is None:
        path = _latest_bronze_file()
        if not path:
            logger.error("No bronze file found — aborting staging.")
            return run_id
        logger.info(f"Reading bronze file: {path}")
        with open(path, encoding="utf-8") as f:
            records = json.load(f)

    if not records:
        logger.warning("Empty record list — nothing to insert.")
        return run_id

    # In incremental mode (DB already has data), fewer new records is normal.
    try:
        from src.utils.db import fetch_all
        existing = fetch_all("SELECT COUNT(*) FROM staging.raw_annonces;")
        is_incremental = bool(existing and existing[0][0] > 0)
    except Exception:
        is_incremental = False

    try:
        validate_bronze(records, is_incremental=is_incremental)
    except BronzeValidationError as e:
        logger.critical(f"❌ BRONZE VALIDATION FAILED: {e}")
        raise

    _qc_report(records)

    # FIX STAGING-DUP: Deduplicate by lien BEFORE bulk insert.
    # PostgreSQL raises "ON CONFLICT DO UPDATE command cannot affect row a second time"
    # when the same lien appears more than once in a single INSERT batch.
    # The scraper returns 141+ duplicates (same listing seen on multiple pages).
    # We keep only the first occurrence per lien — data is identical across
    # duplicates.
    seen_liens: set = set()
    deduped_records = []
    for r in records:
        if r.get("error") is not None:
            continue
        lien = r.get("lien")
        if lien and lien in seen_liens:
            continue
        if lien:
            seen_liens.add(lien)
        deduped_records.append(r)

    n_before = len([r for r in records if r.get("error") is None])
    n_after = len(deduped_records)
    if n_before != n_after:
        logger.info(
            f"Deduplication: {n_before - n_after} duplicate lien(s) removed "
            f"before staging insert ({n_after} unique records remaining)."
        )

    rows = [
        (
            run_id,
            r.get("titre"),
            r.get("prix"),
            r.get("prix_type", "mensuel"),
            r.get("ville"),
            r.get("quartier"),
            r.get("surface"),
            r.get("nb_chambres"),
            r.get("nb_salles_bain"),
            r.get("etage"),
            r.get("lien"),
            r.get("scraped_at"),
        )
        for r in deduped_records
    ]

    bulk_insert(_INSERT, rows)
    logger.info(
        f"=== Staging load finished — {len(rows)} unique rows inserted (run_id={run_id}) ==="
    )
    return run_id
