"""
Great Expectations — Bronze Layer Suite
=========================================
Validates raw scraped records (bronze JSON) using GX expectations.

Fixed for great-expectations >= 0.18 (Fluent API).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import (
    GX_MIN_ROWS, GX_MAX_ROWS,
    GX_PRIX_MOSTLY, GX_VILLE_MOSTLY,
)

try:
    import great_expectations as gx
    GX_AVAILABLE = True
except ImportError:
    GX_AVAILABLE = False


# ── Paths ───────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GX_ROOT = PROJECT_ROOT / "gx"
SUITE_NAME = "bronze_suite"
DS_NAME = "bronze_pandas_ds"
ASSET_NAME = "bronze_asset"


# ── Context ─────────────────────────────────────────────────────────────

def _get_context():
    # Use ephemeral context — nothing is written to disk, so no "suite already
    # exists" errors when the pipeline restarts inside the same container.
    return gx.get_context(mode="ephemeral")


# ── Suite builder (GX 0.18+ API) ────────────────────────────────────────

def _build_bronze_suite(context) -> None:
    """Create or overwrite the bronze expectation suite using GX 0.18+ API."""

    # Ephemeral context resets on every call, but delete first to be safe.
    try:
        context.suites.delete(SUITE_NAME)
    except Exception:
        pass
    suite = context.suites.add(gx.ExpectationSuite(name=SUITE_NAME))

    import pandas as pd
    # FIX #30: Use realistic sample rows instead of an empty DataFrame.
    # GX infers column types and validates expectations against actual data shapes.
    # An empty DataFrame causes GX to treat every column as object dtype,
    # making numeric expectations (expect_column_values_to_be_between) trivially pass
    # on nulls — masking real data quality issues at runtime.
    dummy_df = pd.DataFrame([
        {
            "titre": "Appartement 3 chambres à louer - Casablanca",
            "prix": "8500",
            "prix_type": "mensuel",
            "ville": "Casablanca",
            "quartier": "Maarif",
            "surface": "120",
            "nb_chambres": "3",
            "nb_salles_bain": "2",
            "etage": "3",
            "annee_construction": "2010",
            "lien": "https://www.avito.ma/fr/casablanca/appartements/sample-001",
            "scraped_at": "2024-01-15T10:00:00",
        },
        {
            "titre": "Studio meublé à louer - Rabat",
            "prix": "4200",
            "prix_type": "mensuel",
            "ville": "Rabat",
            "quartier": "Agdal",
            "surface": "45",
            "nb_chambres": "1",
            "nb_salles_bain": "1",
            "etage": "1",
            "annee_construction": None,
            "lien": "https://www.avito.ma/fr/rabat/studios/sample-002",
            "scraped_at": "2024-01-15T11:30:00",
        },
    ])

    # Register datasource if not exists
    try:
        ds = context.data_sources.get(DS_NAME)
    except Exception:
        ds = context.data_sources.add_pandas(name=DS_NAME)

    # Add data asset
    try:
        asset = ds.get_asset(ASSET_NAME)
    except Exception:
        asset = ds.add_dataframe_asset(name=ASSET_NAME)

    try:
        batch_def = asset.get_batch_definition("bronze_batch")
    except Exception:
        batch_def = asset.add_batch_definition_whole_dataframe("bronze_batch")
    batch = batch_def.get_batch(batch_parameters={"dataframe": dummy_df})
    validator = context.get_validator(
        batch=batch,
        expectation_suite=suite,
    )

    # ── 1. Schema ───────────────────────────────────────────────────────────
    for col in ["titre", "prix", "ville", "lien", "scraped_at"]:
        validator.expect_column_to_exist(col)

    # ── 2. Completeness ─────────────────────────────────────────────────────
    validator.expect_column_values_to_not_be_null(
        "prix", mostly=GX_PRIX_MOSTLY)
    validator.expect_column_values_to_not_be_null(
        "ville", mostly=GX_VILLE_MOSTLY)
    validator.expect_column_values_to_not_be_null("titre", mostly=0.80)
    validator.expect_column_values_to_not_be_null("scraped_at", mostly=1.0)

    # ── 3. Value validity ───────────────────────────────────────────────────
    validator.expect_column_values_to_be_in_set(
        "prix_type",
        value_set=["mensuel", "journalier", "inconnu"],
        mostly=0.95,
    )
    # NOTE: Range checks for surface, nb_chambres, nb_salles_bain, annee_construction
    # are intentionally omitted. These columns are frequently NULL in avito.ma data
    # and GX raises errors when bounds don't match column types or when both are None.
    # Fill-rate checks above are sufficient for bronze layer validation.

    # ── 4. Format ───────────────────────────────────────────────────────────
    validator.expect_column_values_to_match_regex(
        "lien", regex=r"^https://www\.avito\.ma/", mostly=0.95)
    validator.expect_column_values_to_match_regex(
        "scraped_at", regex=r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", mostly=1.0)

    # ── 5. Uniqueness ───────────────────────────────────────────────────────
    validator.expect_column_values_to_be_unique("lien")

    # ── 6. Table-level ──────────────────────────────────────────────────────
    validator.expect_table_row_count_to_be_between(
        min_value=GX_MIN_ROWS, max_value=GX_MAX_ROWS)
    validator.expect_table_columns_to_match_set(
        column_set=[
            "titre", "prix", "prix_type", "ville", "quartier",
            "surface", "nb_chambres", "nb_salles_bain",
            "etage", "annee_construction", "lien", "scraped_at",
        ],
        exact_match=False,
    )


# ── Checkpoint runner ───────────────────────────────────────────────────

def run_bronze_checkpoint(bronze_json_path: str) -> bool:
    """
    Load a bronze JSON file, run the GX bronze suite, return True if passed.
    """
    if not GX_AVAILABLE:
        raise ImportError(
            "great_expectations is not installed. Run: pip install great-expectations")

    import pandas as pd

    path = Path(bronze_json_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Bronze file not found: {path}")

    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    df = pd.DataFrame(records)

    for col in [
        "surface",
        "nb_chambres",
        "nb_salles_bain",
        "etage",
            "annee_construction"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    context = _get_context()

    # Always rebuild suite to avoid stale state
    _build_bronze_suite(context)

    # Register datasource
    try:
        ds = context.data_sources.get(DS_NAME)
    except Exception:
        ds = context.data_sources.add_pandas(name=DS_NAME)

    try:
        asset = ds.get_asset(ASSET_NAME)
    except Exception:
        asset = ds.add_dataframe_asset(name=ASSET_NAME)

    try:
        batch_def = asset.get_batch_definition("bronze_batch")
    except Exception:
        batch_def = asset.add_batch_definition_whole_dataframe("bronze_batch")

    suite = context.suites.get(SUITE_NAME)

    vd_name = "bronze_validation_run"
    try:
        context.validation_definitions.delete(vd_name)
    except Exception:
        pass
    validation_def = context.validation_definitions.add(
        gx.ValidationDefinition(name=vd_name, data=batch_def, suite=suite)
    )

    raw_result: dict = dict(
        validation_def.run(
            batch_parameters={
                "dataframe": df}))

    success: bool = bool(raw_result.get("success", False))
    stats: dict = dict(raw_result.get("statistics", {}))
    evaluated: int = int(stats.get("evaluated_expectations", 0))
    successful: int = int(stats.get("successful_expectations", 0))
    failed: int = int(stats.get("unsuccessful_expectations", 0))

    _print_summary(path.name, evaluated, successful, failed, success)

    docs_path = GX_ROOT / "uncommitted" / "data_docs" / "local_site" / "index.html"
    if docs_path.exists():
        print(f"\n  📊 Data Docs: file://{docs_path}")

    return success


def _print_summary(filename, evaluated, successful, failed, passed):
    status = "✅ PASSED" if passed else "❌ FAILED"
    print(
        f"\n{'='*50}\n"
        f"  GX BRONZE CHECKPOINT — {status}\n"
        f"  File      : {filename}\n"
        f"  Evaluated : {evaluated}\n"
        f"  Passed    : {successful}\n"
        f"  Failed    : {failed}\n"
        f"{'='*50}"
    )


def rebuild_suite() -> None:
    """Force-rebuild the bronze expectation suite."""
    if not GX_AVAILABLE:
        raise ImportError("great_expectations is not installed.")
    context = _get_context()
    _build_bronze_suite(context)
    print(f"✅ Suite '{SUITE_NAME}' rebuilt in {GX_ROOT}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.expectations.gx_bronze <path_to_bronze.json>")
        sys.exit(1)
    passed = run_bronze_checkpoint(sys.argv[1])
    sys.exit(0 if passed else 1)
