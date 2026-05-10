import os
from typing import Any
"""
ML Schema — One Big Table (OBT) / Feature Store.
All features in one flat table. No encoding, scaling, or SMOTE here —
those transformations happen in the ML notebook after extraction.

FIX: DDL مفصول في قائمة بدلاً من split(";") الهش
"""

import pandas as pd
from datetime import datetime
import numpy as np
from src.utils.db import get_connection, release_connection, execute_query, bulk_insert
from src.utils.logger import get_logger

logger = get_logger("ml_schema")

GOLD_ML_DIR = os.path.join(os.path.dirname(__file__), "../../data/gold/ml")

# ✅ FIX: DDL مفصول في قائمة — لا split(";") الهش
_DDL_STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS ml_schema;",

    """CREATE TABLE IF NOT EXISTS ml_schema.feature_store (
        id                  SERIAL PRIMARY KEY,

        -- Target variable
        prix                NUMERIC,

        -- Raw features
        ville               TEXT,
        quartier            TEXT,
        surface_m2          NUMERIC,
        nb_chambres         INTEGER,
        nb_salles_bain      INTEGER,
        etage               TEXT,

        -- Engineered features
        prix_par_m2         NUMERIC,
        categorie_prix      TEXT,
        prix_type           TEXT DEFAULT 'mensuel',

        -- Metadata (excluded from model training)
        titre               TEXT,
        lien                TEXT UNIQUE,
        scraped_at          TIMESTAMP,
        loaded_at           TIMESTAMP DEFAULT NOW()
    );""",

    "CREATE INDEX IF NOT EXISTS idx_ml_ville ON ml_schema.feature_store(ville);",
    "CREATE INDEX IF NOT EXISTS idx_ml_prix  ON ml_schema.feature_store(prix);",
    "CREATE INDEX IF NOT EXISTS idx_ml_type  ON ml_schema.feature_store(prix_type);",
]

# ✅ FIX: migration لإضافة prix_type إن لم يكن موجوداً
# FIX #14: Migrations moved to src/utils/migrations.py
_DDL_MIGRATIONS: list[str] = []  # kept for reference only — see utils/migrations.py

_INSERT = """
INSERT INTO ml_schema.feature_store
    (prix, ville, quartier, surface_m2, nb_chambres, nb_salles_bain,
     etage, prix_par_m2, categorie_prix,
     prix_type, titre, lien, scraped_at)
VALUES %s
ON CONFLICT (lien) DO NOTHING
"""

_COLS = [
    "prix", "ville", "quartier", "surface_m2", "nb_chambres",
    "nb_salles_bain", "etage", "prix_par_m2",
    "categorie_prix", "prix_type", "titre", "lien", "scraped_at",
]

INT_MIN = -2_147_483_648
INT_MAX = 2_147_483_647

_INT_COLS = ["nb_chambres", "nb_salles_bain"]


def _safe_int(val: Any) -> int | None:
    """Convert value to safe PostgreSQL INTEGER or None."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        v = int(val)
        if INT_MIN <= v <= INT_MAX:
            return v
        return None
    except (ValueError, TypeError, OverflowError):
        return None


def _fetch_clean() -> pd.DataFrame:
    conn = get_connection()
    try:
        con = conn.connection  # type: ignore[attr-defined]
        return pd.read_sql("SELECT * FROM clean.annonces", con)
    finally:
        release_connection(conn)


def run_ml_schema(df: pd.DataFrame | None = None) -> None:
    logger.info("=== ML Schema load started ===")

    # ✅ FIX: تنفيذ كل جملة DDL بشكل مستقل
    for stmt in _DDL_STATEMENTS:
        execute_query(stmt)

    for migration in _DDL_MIGRATIONS:
        try:
            execute_query(migration)
        except Exception as e:
            logger.debug(f"Migration skipped: {e}")

    logger.info("ML Schema DDL applied.")

    if df is None:
        df = _fetch_clean()
        logger.info(f"Loaded {len(df)} rows from clean.annonces")

    # ✅ FIX: إضافة prix_type إن لم يكن موجوداً في df
    if "prix_type" not in df.columns:
        df = df.copy()
        df["prix_type"] = "mensuel"

    missing = [c for c in _COLS if c not in df.columns]
    if missing:
        logger.error(f"Missing columns in DataFrame: {missing}")
        return

    if df.empty:
        logger.warning("DataFrame is empty — skipping ML schema load.")
        return

    null_prix = df["prix"].isna().sum()
    total = len(df)
    logger.info(
        f"Target variable (prix): {total - null_prix}/{total} valid values")

    if null_prix == total:
        logger.warning(
            "All prix values are NULL — skipping feature store load.")
        return

    df = df.copy()
    for col in _INT_COLS:
        if col in df.columns:
            df[col] = df[col].apply(_safe_int)

    sub = df[_COLS].where(pd.notna(df[_COLS]), other=float("nan"))
    rows = [tuple(r) for r in sub.itertuples(index=False, name=None)]

    safe_rows = []
    for row in rows:
        safe_row: list[Any] = []
        for i, val in enumerate(row):
            col = _COLS[i]
            if isinstance(val, (float,)) and val != val:  # NaN check
                safe_row.append(None)
            elif isinstance(val, (int, float)) and col not in _INT_COLS:
                safe_row.append(
                    None if (
                        isinstance(
                            val,
                            float) and val != val) else val)
            else:
                safe_row.append(val)
        safe_rows.append(tuple(safe_row))

    bulk_insert(_INSERT, safe_rows)
    logger.info(
        f"=== ML Schema load finished — {len(safe_rows)} rows in feature_store ==="
    )
    _save_gold_ml(df[_COLS].where(pd.notna(df[_COLS]), other=float("nan")))


def _save_gold_ml(df: pd.DataFrame) -> None:
    """
    Export ML gold layer partitioned by date only:
      data/gold/ml/YYYY/MM/DD/feature_store_<ts>.csv
      data/gold/ml/YYYY/MM/DD/feature_store_<ts>.parquet
    """
    from datetime import timezone
    if df.empty:
        logger.error(
            "Gold ML: DataFrame is empty — nothing to export. "
            "Check that run_clean() produced data before run_ml_schema()."
        )
        return

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    date_pfx = datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
    part_dir = os.path.join(GOLD_ML_DIR, date_pfx)
    os.makedirs(part_dir, exist_ok=True)

    stem = f"feature_store_{ts}"

    # CSV
    csv_path = os.path.join(part_dir, f"{stem}.csv")
    try:
        df.to_csv(csv_path, index=False, encoding="utf-8")
        logger.info(f"Gold ML CSV     → {csv_path}  ({len(df)} rows)")
    except Exception as e:
        logger.warning(f"Gold ML CSV export failed: {e}")

    # Parquet
    parquet_path = os.path.join(part_dir, f"{stem}.parquet")
    try:
        df.to_parquet(parquet_path, index=False, engine="pyarrow")
        logger.info(f"Gold ML Parquet → {parquet_path}  ({len(df)} rows)")
    except Exception as e:
        logger.warning(
            f"Gold ML Parquet skipped (pyarrow not installed?): {e}")