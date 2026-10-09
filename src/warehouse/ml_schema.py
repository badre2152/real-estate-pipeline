"""Load the machine learning feature store and export snapshots."""

import os
from typing import Any
import pandas as pd
from datetime import datetime
import numpy as np
from src.utils.db import get_connection, release_connection, execute_query, bulk_insert
from src.utils.logger import get_logger

logger = get_logger("ml_schema")

GOLD_ML_DIR = os.path.join(os.path.dirname(__file__), "../../data/gold/ml")

_DDL_STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS ml_schema;",

    """CREATE TABLE IF NOT EXISTS ml_schema.feature_store (
        id                  SERIAL PRIMARY KEY,

        prix                NUMERIC,

        ville               TEXT,
        quartier            TEXT,
        surface_m2          NUMERIC,
        nb_chambres         INTEGER,
        nb_salles_bain      INTEGER,
        etage               TEXT,

        prix_par_m2         NUMERIC,
        categorie_prix      TEXT,
        prix_type           TEXT DEFAULT 'mensuel',

        titre               TEXT,
        lien                TEXT UNIQUE,
        scraped_at          TIMESTAMP,
        loaded_at           TIMESTAMP DEFAULT NOW()
    );""",

    "CREATE INDEX IF NOT EXISTS idx_ml_ville ON ml_schema.feature_store(ville);",
    "CREATE INDEX IF NOT EXISTS idx_ml_prix  ON ml_schema.feature_store(prix);",
    "CREATE INDEX IF NOT EXISTS idx_ml_type  ON ml_schema.feature_store(prix_type);",
]

_INSERT = """
INSERT INTO ml_schema.feature_store
    (prix, ville, quartier, surface_m2, nb_chambres, nb_salles_bain,
     etage, prix_par_m2, categorie_prix,
     prix_type, titre, lien, scraped_at)
VALUES %s
ON CONFLICT (lien) DO UPDATE SET
    prix           = EXCLUDED.prix,
    ville          = EXCLUDED.ville,
    quartier       = EXCLUDED.quartier,
    surface_m2     = EXCLUDED.surface_m2,
    nb_chambres    = EXCLUDED.nb_chambres,
    nb_salles_bain = EXCLUDED.nb_salles_bain,
    etage          = EXCLUDED.etage,
    prix_par_m2    = EXCLUDED.prix_par_m2,
    categorie_prix = EXCLUDED.categorie_prix,
    prix_type      = EXCLUDED.prix_type,
    titre          = EXCLUDED.titre,
    scraped_at     = EXCLUDED.scraped_at,
    loaded_at      = NOW()
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

    for stmt in _DDL_STATEMENTS:
        execute_query(stmt)

    logger.info("ML Schema DDL applied.")

    if df is None:
        df = _fetch_clean()
        logger.info(f"Loaded {len(df)} rows from clean.annonces")

    if "prix_type" not in df.columns:
        df = df.copy()
        df["prix_type"] = "mensuel"

    missing = [c for c in _COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in DataFrame: {missing}")

    if df.empty:
        raise ValueError("Cannot load an empty ML dataset")

    null_prix = df["prix"].isna().sum()
    total = len(df)
    logger.info(
        f"Target variable (prix): {total - null_prix}/{total} valid values")

    if null_prix == total:
        raise ValueError("Cannot load ML data without valid prices")

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
            if isinstance(val, (float,)) and val != val:
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
        f"=== ML Schema load finished: {len(safe_rows)} rows in feature_store ==="
    )
    _save_gold_ml(df[_COLS].where(pd.notna(df[_COLS]), other=float("nan")))


def _save_gold_ml(df: pd.DataFrame) -> None:
    """Export the current ML dataset to CSV and Parquet."""
    from datetime import timezone
    if df.empty:
        raise ValueError("Cannot export an empty ML dataset")

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    date_pfx = datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
    part_dir = os.path.join(GOLD_ML_DIR, date_pfx)
    os.makedirs(part_dir, exist_ok=True)

    stem = f"feature_store_{ts}"

    csv_path = os.path.join(part_dir, f"{stem}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8")
    logger.info("Gold ML CSV saved to %s (%s rows)", csv_path, len(df))

    parquet_path = os.path.join(part_dir, f"{stem}.parquet")
    df.to_parquet(parquet_path, index=False, engine="pyarrow")
    logger.info("Gold ML Parquet saved to %s (%s rows)", parquet_path, len(df))
