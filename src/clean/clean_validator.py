"""
Staging → Cleaning Validation
================================
Validates the cleaned DataFrame AFTER transformation and BEFORE it is written
to clean.annonces or data/silver/.

Two checkpoints are provided:

  1. validate_pre_clean(df_staging)
     ─ Called on the raw staging DataFrame before _clean() runs.
     ─ Checks that staging data is suitable for transformation.

  2. validate_post_clean(df_clean)
     ─ Called on the cleaned DataFrame after _clean() runs.
     ─ Checks that transformations produced correct, consistent output.
     ─ This is the main gate before any data reaches the DB or silver CSV.

Design:
  Hard rules  → raise CleanValidationError  → pipeline ABORTS
  Soft rules  → log warnings                → pipeline CONTINUES

Usage (called automatically inside run_clean):
    from src.clean.clean_validator import validate_pre_clean, validate_post_clean, CleanValidationError
    validate_pre_clean(df_staging)
    df_clean = _clean(df_staging)
    validate_post_clean(df_clean)
"""

from __future__ import annotations

import re
from datetime import datetime

import numpy as np
import pandas as pd

from src.utils.logger import get_logger

logger = get_logger("clean_validator")


# ── Custom exception ──────────────────────────────────────────────────────────

class CleanValidationError(Exception):
    """Raised when a hard validation rule fails. Stops the pipeline."""


# ── Thresholds ────────────────────────────────────────────────────────────────

# Pre-clean (staging)
HARD_MIN_STAGING_ROWS       = 3     # abort if staging is nearly empty
HARD_MIN_PRIX_FILL_STAGING  = 0.50  # abort if <50% staging rows have prix

# Post-clean (cleaned output)
HARD_MIN_CLEAN_ROWS         = 2     # abort if cleaning wiped almost everything
HARD_MAX_DROP_RATE          = 0.80  # abort if >80% of staging rows were dropped
HARD_MIN_PRIX_FILL_CLEAN    = 0.90  # abort if <90% of clean rows have prix
HARD_MIN_VILLE_FILL_CLEAN   = 0.90  # abort if <90% of clean rows have ville

SOFT_PRIX_M2_FILL           = 0.50  # warn if prix_par_m2 < 50% filled
SOFT_MAX_OUTLIER_PCT        = 0.15  # warn if >15% rows have extreme prix

VALID_PRIX_TYPES    = {"mensuel", "journalier", "journalier_suspect", "inconnu"}
VALID_CATEGORIES    = {"Très Bas", "Bas", "Moyen", "Élevé", "Luxe", "Inconnu"}
VALID_REGIONS       = {
    "Casablanca-Settat", "Rabat-Salé-Kénitra", "Marrakech-Safi",
    "Fès-Meknès", "Tanger-Tétouan-Al Hoceïma", "Souss-Massa",
    "L'Oriental", "Béni Mellal-Khénifra", "Dakhla-Oued Ed-Dahab",
    "Laâyoune-Sakia El Hamra", "Drâa-Tafilalet", "Guelmim-Oued Noun",
    "Autre",
}
ARTEFACT_VILLES     = {"COURS ET FORMATIONS", "Cours Et Formations", "cours et formations"}
AVITO_BASE_URL      = "https://www.avito.ma"
CURRENT_YEAR        = datetime.now().year


# ══════════════════════════════════════════════════════════════════════════════
# PRE-CLEAN RULES  (on staging DataFrame)
# ══════════════════════════════════════════════════════════════════════════════

def _pre_min_rows(df: pd.DataFrame) -> None:
    """HARD: staging must have enough rows to process."""
    if len(df) < HARD_MIN_STAGING_ROWS:
        raise CleanValidationError(
            f"Staging has only {len(df)} row(s) — minimum required is "
            f"{HARD_MIN_STAGING_ROWS}. Nothing to clean."
        )
    logger.info(f"  ✅ staging row count: {len(df)} (≥ {HARD_MIN_STAGING_ROWS})")


def _pre_required_columns(df: pd.DataFrame) -> None:
    """HARD: staging must contain the minimum expected columns."""
    REQUIRED = {"prix", "ville", "lien"}
    missing = REQUIRED - set(df.columns)
    if missing:
        raise CleanValidationError(
            f"Staging DataFrame missing required columns: {missing}"
        )
    logger.info(f"  ✅ required staging columns present: {REQUIRED}")


def _pre_prix_fill(df: pd.DataFrame) -> None:
    """HARD: if most rows have no prix, cleaning will produce nothing useful."""
    filled = df["prix"].notna().sum()
    pct = filled / len(df)
    if pct < HARD_MIN_PRIX_FILL_STAGING:
        raise CleanValidationError(
            f"Staging prix fill rate is {pct:.0%} — below hard minimum "
            f"{HARD_MIN_PRIX_FILL_STAGING:.0%}. Cleaning would be meaningless."
        )
    logger.info(f"  ✅ staging prix fill rate: {pct:.0%} ({filled}/{len(df)})")


def _pre_no_all_null_rows(df: pd.DataFrame) -> None:
    """SOFT: warn about completely empty rows."""
    key_cols = [c for c in ["prix", "ville", "surface", "lien"] if c in df.columns]
    all_null = df[key_cols].isna().all(axis=1).sum()
    if all_null > 0:
        logger.warning(
            f"  ⚠️  {all_null} row(s) have all key fields null in staging."
        )
    else:
        logger.info(f"  ✅ no fully-null rows in staging")


def _pre_lien_format(df: pd.DataFrame) -> None:
    """SOFT: liens in staging should point to avito.ma."""
    if "lien" not in df.columns:
        return
    bad = df[~df["lien"].fillna("").str.startswith(AVITO_BASE_URL)]
    if len(bad) > 0:
        logger.warning(
            f"  ⚠️  {len(bad)} staging row(s) have non-avito lien values."
        )
    else:
        logger.info(f"  ✅ all staging liens point to avito.ma")


def _pre_artefact_villes(df: pd.DataFrame) -> None:
    """SOFT: count known artefact villes — they will be dropped during cleaning."""
    if "ville" not in df.columns:
        return
    artefacts = df[df["ville"].isin(ARTEFACT_VILLES)]
    if len(artefacts) > 0:
        logger.warning(
            f"  ⚠️  {len(artefacts)} staging row(s) with artefact ville "
            f"(e.g. 'COURS ET FORMATIONS') — will be dropped by cleaning."
        )
    else:
        logger.info(f"  ✅ no artefact villes in staging")


# ══════════════════════════════════════════════════════════════════════════════
# POST-CLEAN RULES  (on cleaned DataFrame)
# ══════════════════════════════════════════════════════════════════════════════

def _post_min_rows(df_clean: pd.DataFrame, n_staging: int) -> None:
    """HARD: cleaning must not drop too many rows."""
    n = len(df_clean)
    if n < HARD_MIN_CLEAN_ROWS:
        raise CleanValidationError(
            f"Cleaned DataFrame has only {n} row(s). "
            f"Cleaning removed almost everything — check transformations."
        )

    drop_rate = 1 - (n / max(n_staging, 1))
    if drop_rate > HARD_MAX_DROP_RATE:
        raise CleanValidationError(
            f"Cleaning dropped {drop_rate:.0%} of staging rows "
            f"({n_staging} → {n}). Hard limit is {HARD_MAX_DROP_RATE:.0%}. "
            f"Possible transformation bug."
        )

    logger.info(
        f"  ✅ row count after cleaning: {n} "
        f"(drop rate: {drop_rate:.0%}, staging was {n_staging})"
    )


def _post_required_columns(df: pd.DataFrame) -> None:
    """HARD: cleaned DataFrame must contain all expected output columns."""
    REQUIRED = {
        "prix", "prix_type", "ville", "lien", "scraped_at",
        "surface_m2", "prix_par_m2", "categorie_prix",
        "region_label", "is_grande_ville",
    }
    missing = REQUIRED - set(df.columns)
    if missing:
        raise CleanValidationError(
            f"Cleaned DataFrame missing required columns: {missing}. "
            f"A transformation step may have failed silently."
        )
    logger.info(f"  ✅ all required output columns present")


def _post_no_raw_surface_col(df: pd.DataFrame) -> None:
    """HARD: raw 'surface' column must not survive into cleaned output."""
    if "surface" in df.columns and "surface_m2" in df.columns:
        raise CleanValidationError(
            "Both 'surface' (raw) and 'surface_m2' (clean) exist in cleaned "
            "DataFrame. Raw column must be dropped in _clean()."
        )
    logger.info(f"  ✅ raw 'surface' column correctly dropped")


def _post_prix_fill(df: pd.DataFrame) -> None:
    """HARD: virtually all clean rows must have a valid prix."""
    filled = df["prix"].notna().sum()
    pct = filled / len(df)
    if pct < HARD_MIN_PRIX_FILL_CLEAN:
        raise CleanValidationError(
            f"Cleaned prix fill rate is {pct:.0%} — below hard minimum "
            f"{HARD_MIN_PRIX_FILL_CLEAN:.0%}. Transformation may have broken prix parsing."
        )
    logger.info(f"  ✅ clean prix fill rate: {pct:.0%} ({filled}/{len(df)})")


def _post_ville_fill(df: pd.DataFrame) -> None:
    """HARD: virtually all clean rows must have a valid ville."""
    filled = (df["ville"].notna() & (df["ville"].str.strip() != "")).sum()
    pct = filled / len(df)
    if pct < HARD_MIN_VILLE_FILL_CLEAN:
        raise CleanValidationError(
            f"Cleaned ville fill rate is {pct:.0%} — below hard minimum "
            f"{HARD_MIN_VILLE_FILL_CLEAN:.0%}."
        )
    logger.info(f"  ✅ clean ville fill rate: {pct:.0%} ({filled}/{len(df)})")


def _post_prix_positive(df: pd.DataFrame) -> None:
    """HARD: no prix value should be zero or negative after cleaning."""
    bad = df[df["prix"].notna() & (df["prix"] <= 0)]
    if len(bad) > 0:
        raise CleanValidationError(
            f"{len(bad)} row(s) have prix ≤ 0 after cleaning. "
            f"The _apply_missing_value_strategy filter failed. "
            f"Values: {bad['prix'].tolist()}"
        )
    logger.info(f"  ✅ all prix values are positive")


def _post_no_duplicate_liens(df: pd.DataFrame) -> None:
    """HARD: lien must be unique in clean output (it's a DB UNIQUE key)."""
    dups = df[df.duplicated(subset=["lien"], keep=False)]
    if len(dups) > 0:
        raise CleanValidationError(
            f"{len(dups)} duplicate lien(s) found in cleaned output. "
            f"Dedup step in _clean() failed. Examples: {dups['lien'].head(2).tolist()}"
        )
    logger.info(f"  ✅ no duplicate liens in cleaned output")


def _post_no_artefact_villes(df: pd.DataFrame) -> None:
    """HARD: artefact villes must be filtered out during cleaning."""
    remaining = df[df["ville"].isin(ARTEFACT_VILLES)]
    if len(remaining) > 0:
        raise CleanValidationError(
            f"{len(remaining)} artefact ville row(s) survived cleaning "
            f"(e.g. 'COURS ET FORMATIONS'). "
            f"_apply_missing_value_strategy must filter these."
        )
    logger.info(f"  ✅ no artefact villes survived cleaning")


def _post_prix_type_values(df: pd.DataFrame) -> None:
    """SOFT: prix_type should only contain known enum values."""
    if "prix_type" not in df.columns:
        logger.warning("  ⚠️  prix_type column missing from cleaned output")
        return
    invalid = df[~df["prix_type"].isin(VALID_PRIX_TYPES)]
    if len(invalid) > 0:
        logger.warning(
            f"  ⚠️  {len(invalid)} row(s) have unknown prix_type after cleaning: "
            f"{invalid['prix_type'].unique().tolist()}"
        )
    else:
        logger.info(f"  ✅ all prix_type values valid")


def _post_categorie_prix_values(df: pd.DataFrame) -> None:
    """SOFT: categorie_prix must only contain known enum values."""
    if "categorie_prix" not in df.columns:
        return
    invalid = df[df["categorie_prix"].notna() & ~df["categorie_prix"].isin(VALID_CATEGORIES)]
    if len(invalid) > 0:
        logger.warning(
            f"  ⚠️  {len(invalid)} row(s) have unknown categorie_prix: "
            f"{invalid['categorie_prix'].unique().tolist()}"
        )
    else:
        logger.info(f"  ✅ all categorie_prix values valid")


def _post_region_label_values(df: pd.DataFrame) -> None:
    """SOFT: region_label must only contain known Moroccan regions."""
    if "region_label" not in df.columns:
        return
    invalid = df[df["region_label"].notna() & ~df["region_label"].isin(VALID_REGIONS)]
    if len(invalid) > 0:
        logger.warning(
            f"  ⚠️  {len(invalid)} row(s) have unknown region_label: "
            f"{invalid['region_label'].unique().tolist()}"
        )
    else:
        logger.info(f"  ✅ all region_label values valid")


def _post_prix_par_m2_consistency(df: pd.DataFrame) -> None:
    """SOFT: prix_par_m2 must be consistent with prix / surface_m2."""
    if "prix_par_m2" not in df.columns or "surface_m2" not in df.columns:
        return

    check = df[
        df["prix"].notna() &
        df["surface_m2"].notna() &
        df["prix_par_m2"].notna() &
        (df["surface_m2"] > 0)
    ].copy()

    if len(check) == 0:
        logger.info(f"  ✅ prix_par_m2 consistency: no rows to check")
        return

    expected = (check["prix"] / check["surface_m2"]).round(2)
    delta    = (expected - check["prix_par_m2"]).abs()
    bad      = delta[delta > 1.0]   # tolerance: 1 DH/m²

    if len(bad) > 0:
        logger.warning(
            f"  ⚠️  {len(bad)} row(s) have prix_par_m2 inconsistent with "
            f"prix / surface_m2 (tolerance = 1 DH/m²). "
            f"Max deviation: {delta.max():.2f} DH/m²"
        )
    else:
        logger.info(f"  ✅ prix_par_m2 is consistent with prix / surface_m2 on all rows")


def _post_age_bien_valid(df: pd.DataFrame) -> None:
    """SOFT: age_bien must not be negative or absurdly large."""
    if "age_bien" not in df.columns:
        return
    bad_negative = df[df["age_bien"].notna() & (df["age_bien"] < 0)]
    bad_large    = df[df["age_bien"].notna() & (df["age_bien"] > 200)]

    if len(bad_negative) > 0:
        logger.warning(
            f"  ⚠️  {len(bad_negative)} row(s) have negative age_bien "
            f"(future annee_construction). Values: {bad_negative['age_bien'].tolist()}"
        )
    elif len(bad_large) > 0:
        logger.warning(
            f"  ⚠️  {len(bad_large)} row(s) have age_bien > 200 years "
            f"(likely data error). Values: {bad_large['age_bien'].tolist()}"
        )
    else:
        logger.info(f"  ✅ age_bien values are all in valid range")


def _post_surface_positive(df: pd.DataFrame) -> None:
    """SOFT: surface_m2 must be positive where present."""
    if "surface_m2" not in df.columns:
        return
    bad = df[df["surface_m2"].notna() & (df["surface_m2"] <= 0)]
    if len(bad) > 0:
        logger.warning(
            f"  ⚠️  {len(bad)} row(s) have surface_m2 ≤ 0 after cleaning: "
            f"{bad['surface_m2'].tolist()}"
        )
    else:
        logger.info(f"  ✅ all surface_m2 values are positive")


def _post_nb_fields_range(df: pd.DataFrame) -> None:
    """SOFT: nb_chambres and nb_salles_bain must be in sensible ranges."""
    checks = {
        "nb_chambres"    : (1, 20),
        "nb_salles_bain" : (1, 10),
    }
    for col, (lo, hi) in checks.items():
        if col not in df.columns:
            continue
        bad = df[df[col].notna() & ((df[col] < lo) | (df[col] > hi))]
        if len(bad) > 0:
            logger.warning(
                f"  ⚠️  {len(bad)} row(s) have {col} outside [{lo}, {hi}]: "
                f"{bad[col].tolist()}"
            )
        else:
            logger.info(f"  ✅ {col} values all in range [{lo}, {hi}]")


def _post_scraped_at_valid(df: pd.DataFrame) -> None:
    """SOFT: scraped_at must be a valid past timestamp."""
    if "scraped_at" not in df.columns:
        return
    now = pd.Timestamp.now()
    col = pd.to_datetime(df["scraped_at"], errors="coerce")
    future = df[col > now]
    null_ts = df[col.isna()]
    if len(future) > 0:
        logger.warning(f"  ⚠️  {len(future)} row(s) have future scraped_at timestamps")
    elif len(null_ts) > 0:
        logger.warning(f"  ⚠️  {len(null_ts)} row(s) have unparseable scraped_at")
    else:
        logger.info(f"  ✅ all scraped_at timestamps are valid and in the past")


def _post_fill_rate_summary(df: pd.DataFrame) -> None:
    """INFO: log fill-rate for every output column (for monitoring)."""
    n = len(df)
    COLS = [
        "prix", "prix_type", "ville", "quartier", "surface_m2",
        "nb_chambres", "nb_salles_bain", "etage", "prix_par_m2",
        "annee_construction", "age_bien", "categorie_prix",
        "region_label", "is_grande_ville",
    ]
    lines = [f"  Post-clean fill rates ({n} rows):"]
    for col in COLS:
        if col not in df.columns:
            lines.append(f"    ❓ {col:<22}: not found")
            continue
        filled = df[col].notna().sum()
        pct    = 100 * filled / n
        status = "✅" if pct >= 80 else ("⚠️" if pct >= 40 else "❌")
        lines.append(f"    {status} {col:<22}: {filled}/{n} ({pct:.0f}%)")
    logger.info("\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════

def validate_pre_clean(df_staging: pd.DataFrame) -> None:
    """
    Validate staging DataFrame before _clean() runs.
    Raises CleanValidationError on hard failure.
    """
    logger.info("=" * 50)
    logger.info("  STAGING PRE-CLEAN VALIDATION")
    logger.info("=" * 50)

    hard_rules = [
        ("min_rows"          , lambda: _pre_min_rows(df_staging)),
        ("required_columns"  , lambda: _pre_required_columns(df_staging)),
        ("prix_fill"         , lambda: _pre_prix_fill(df_staging)),
    ]
    soft_rules = [
        ("no_all_null_rows"  , lambda: _pre_no_all_null_rows(df_staging)),
        ("lien_format"       , lambda: _pre_lien_format(df_staging)),
        ("artefact_villes"   , lambda: _pre_artefact_villes(df_staging)),
    ]

    for name, fn in hard_rules:
        try:
            fn()
        except CleanValidationError:
            raise
        except Exception as e:
            raise CleanValidationError(f"Unexpected error in pre-clean rule '{name}': {e}") from e

    for name, fn in soft_rules:
        try:
            fn()
        except Exception as e:
            logger.warning(f"  ⚠️  soft pre-clean rule '{name}' raised: {e}")

    logger.info("  PRE-CLEAN VALIDATION ✅ PASSED\n")


def validate_post_clean(df_clean: pd.DataFrame, n_staging: int) -> dict:
    """
    Validate cleaned DataFrame after _clean() runs.
    Raises CleanValidationError on hard failure.
    Returns summary dict.
    """
    logger.info("=" * 50)
    logger.info("  CLEANING POST-CLEAN VALIDATION")
    logger.info("=" * 50)

    soft_warnings = 0

    hard_rules = [
        ("min_rows"             , lambda: _post_min_rows(df_clean, n_staging)),
        ("required_columns"     , lambda: _post_required_columns(df_clean)),
        ("no_raw_surface_col"   , lambda: _post_no_raw_surface_col(df_clean)),
        ("prix_fill"            , lambda: _post_prix_fill(df_clean)),
        ("ville_fill"           , lambda: _post_ville_fill(df_clean)),
        ("prix_positive"        , lambda: _post_prix_positive(df_clean)),
        ("no_duplicate_liens"   , lambda: _post_no_duplicate_liens(df_clean)),
        ("no_artefact_villes"   , lambda: _post_no_artefact_villes(df_clean)),
    ]
    soft_rules = [
        ("prix_type_values"     , lambda: _post_prix_type_values(df_clean)),
        ("categorie_prix"       , lambda: _post_categorie_prix_values(df_clean)),
        ("region_label"         , lambda: _post_region_label_values(df_clean)),
        ("prix_par_m2"          , lambda: _post_prix_par_m2_consistency(df_clean)),
        ("age_bien"             , lambda: _post_age_bien_valid(df_clean)),
        ("surface_positive"     , lambda: _post_surface_positive(df_clean)),
        ("nb_fields_range"      , lambda: _post_nb_fields_range(df_clean)),
        ("scraped_at"           , lambda: _post_scraped_at_valid(df_clean)),
        ("fill_rate_summary"    , lambda: _post_fill_rate_summary(df_clean)),
    ]

    for name, fn in hard_rules:
        try:
            fn()
        except CleanValidationError:
            raise
        except Exception as e:
            raise CleanValidationError(
                f"Unexpected error in post-clean rule '{name}': {e}"
            ) from e

    for name, fn in soft_rules:
        try:
            fn()
        except Exception as e:
            soft_warnings += 1
            logger.warning(f"  ⚠️  soft post-clean rule '{name}' raised: {e}")

    summary = {
        "clean_rows"    : len(df_clean),
        "staging_rows"  : n_staging,
        "drop_rate"     : round(1 - len(df_clean) / max(n_staging, 1), 3),
        "soft_warnings" : soft_warnings,
    }

    logger.info(
        f"\n  POST-CLEAN VALIDATION SUMMARY\n"
        f"  ─────────────────────────────\n"
        f"  Staging rows   : {summary['staging_rows']}\n"
        f"  Clean rows     : {summary['clean_rows']}\n"
        f"  Drop rate      : {summary['drop_rate']:.0%}\n"
        f"  Soft warnings  : {summary['soft_warnings']}\n"
        f"  STATUS         : ✅ PASSED\n"
        f"  {'=' * 48}"
    )

    return summary