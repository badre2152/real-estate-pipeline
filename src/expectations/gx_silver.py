"""
Great Expectations — Silver (Clean) Layer Suite
=================================================
Validates the cleaned DataFrame (silver layer) using GX expectations.

Fixed for great-expectations >= 0.18 (Fluent API).
"""

from __future__ import annotations

import datetime
from pathlib import Path

from src.config import (
    GX_SILVER_MOSTLY, GX_SURFACE_MOSTLY,
    MIN_PRIX, MAX_PRIX, MIN_SURFACE, MAX_SURFACE,
)

try:
    import great_expectations as gx
    GX_AVAILABLE = True
except ImportError:
    GX_AVAILABLE = False

import pandas as pd


# ── Paths ───────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GX_ROOT = PROJECT_ROOT / "gx"
SUITE_NAME = "silver_suite"
DS_NAME = "silver_pandas_ds"
ASSET_NAME = "silver_asset"
CURRENT_YEAR = datetime.datetime.now().year


# ── Context ─────────────────────────────────────────────────────────────

def _get_context():
    # Use ephemeral context — nothing is written to disk, so no "suite already
    # exists" errors when the pipeline restarts inside the same container.
    return gx.get_context(mode="ephemeral")


# ── Suite builder (GX 0.18+ API) ────────────────────────────────────────

def _build_silver_suite(context) -> None:
    """Create or overwrite the silver expectation suite using GX 0.18+ API."""

    # Ephemeral context resets on every call, but delete first to be safe.
    try:
        context.suites.delete(SUITE_NAME)
    except Exception:
        pass
    suite = context.suites.add(gx.ExpectationSuite(name=SUITE_NAME))

    dummy_df = pd.DataFrame(columns=[
        "prix", "prix_type", "ville", "quartier", "surface_m2",
        "nb_chambres", "nb_salles_bain", "etage", "lien", "scraped_at",
        "prix_par_m2", "categorie_prix", "region_label", "is_grande_ville",
        "age_bien", "annee_construction",
    ])

    try:
        ds = context.data_sources.get(DS_NAME)
    except Exception:
        ds = context.data_sources.add_pandas(name=DS_NAME)

    try:
        asset = ds.get_asset(ASSET_NAME)
    except Exception:
        asset = ds.add_dataframe_asset(name=ASSET_NAME)

    try:
        batch_def = asset.get_batch_definition("silver_batch")
    except Exception:
        batch_def = asset.add_batch_definition_whole_dataframe("silver_batch")

    batch = batch_def.get_batch(batch_parameters={"dataframe": dummy_df})
    validator = context.get_validator(
        batch=batch,
        expectation_suite=suite,
    )

    # ── 1. Schema ───────────────────────────────────────────────────────────
    for col in [
        "prix", "prix_type", "ville", "lien", "scraped_at",
        "surface_m2", "prix_par_m2", "categorie_prix",
        "region_label", "is_grande_ville",
    ]:
        validator.expect_column_to_exist(col)

    # ── 2. Completeness ─────────────────────────────────────────────────────
    validator.expect_column_values_to_not_be_null("prix", mostly=0.99)
    validator.expect_column_values_to_not_be_null("ville", mostly=0.99)
    validator.expect_column_values_to_not_be_null("lien", mostly=1.0)
    validator.expect_column_values_to_not_be_null("prix_type", mostly=1.0)

    # ── 3. Numeric ranges ───────────────────────────────────────────────────
    # Only apply range checks to columns that are always populated (prix, surface_m2,
    # prix_par_m2). Columns that are frequently NULL (nb_chambres, nb_salles_bain,
    # age_bien, annee_construction) are skipped here — GX raises errors when
    # both bounds are None or when bound types don't match nullable column
    # types.
    validator.expect_column_values_to_be_between(
        "prix",
        min_value=MIN_PRIX,
        max_value=MAX_PRIX,
        mostly=GX_SILVER_MOSTLY)
    validator.expect_column_values_to_be_between(
        "surface_m2",
        min_value=MIN_SURFACE,
        max_value=MAX_SURFACE,
        mostly=GX_SURFACE_MOSTLY)
    validator.expect_column_values_to_be_between(
        "prix_par_m2", min_value=1.0, max_value=50_000.0, mostly=0.90)

    # ── 4. Categorical sets ─────────────────────────────────────────────────
    validator.expect_column_values_to_be_in_set(
        "prix_type",
        value_set=["mensuel", "journalier", "inconnu"],
        mostly=1.0,
    )
    validator.expect_column_values_to_be_in_set(
        "categorie_prix",
        value_set=["Très Bas", "Bas", "Moyen", "Élevé", "Luxe", "Inconnu"],
        mostly=1.0,
    )
    validator.expect_column_values_to_be_in_set(
        "region_label",
        value_set=[
            "Casablanca-Settat", "Rabat-Salé-Kénitra", "Marrakech-Safi",
            "Fès-Meknès", "Tanger-Tétouan-Al Hoceïma", "Souss-Massa",
            "L'Oriental", "Béni Mellal-Khénifra", "Dakhla-Oued Ed-Dahab",
            "Laâyoune-Sakia El Hamra", "Drâa-Tafilalet", "Guelmim-Oued Noun",
            "Autre",
        ],
        mostly=1.0,
    )
    validator.expect_column_values_to_be_in_set(
        "is_grande_ville",
        value_set=[True, False, 0, 1],
        mostly=1.0,
    )

    # ── 5. Format ───────────────────────────────────────────────────────────
    validator.expect_column_values_to_match_regex(
        "lien", regex=r"^https://www\.avito\.ma/", mostly=1.0)
    validator.expect_column_values_to_match_regex(
        "scraped_at", regex=r"^\d{4}-\d{2}-\d{2}", mostly=1.0)

    # ── 6. Uniqueness ───────────────────────────────────────────────────────
    validator.expect_column_values_to_be_unique("lien")

    # ── 7. Table-level ──────────────────────────────────────────────────────
    validator.expect_table_row_count_to_be_between(
        min_value=2, max_value=100_000)


# ── Checkpoint runner ───────────────────────────────────────────────────

def run_silver_checkpoint(
        df_clean: pd.DataFrame,
        run_label: str = "silver") -> bool:
    """
    Validate a cleaned DataFrame using the GX silver suite.
    Returns True if all expectations pass.
    """
    if not GX_AVAILABLE:
        raise ImportError(
            "great_expectations is not installed. Run: pip install great-expectations")

    context = _get_context()

    # Always rebuild suite to avoid stale state
    _build_silver_suite(context)

    try:
        ds = context.data_sources.get(DS_NAME)
    except Exception:
        ds = context.data_sources.add_pandas(name=DS_NAME)

    try:
        asset = ds.get_asset(ASSET_NAME)
    except Exception:
        asset = ds.add_dataframe_asset(name=ASSET_NAME)

    try:
        batch_def = asset.get_batch_definition("silver_batch")
    except Exception:
        batch_def = asset.add_batch_definition_whole_dataframe("silver_batch")

    suite = context.suites.get(SUITE_NAME)

    vd_name = "silver_validation_run"
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
                "dataframe": df_clean.copy()}))

    success: bool = bool(raw_result.get("success", False))
    stats: dict = dict(raw_result.get("statistics", {}))
    evaluated: int = int(stats.get("evaluated_expectations", 0))
    successful: int = int(stats.get("successful_expectations", 0))
    failed: int = int(stats.get("unsuccessful_expectations", 0))

    _print_summary(run_label, evaluated, successful, failed, success)

    docs_path = GX_ROOT / "uncommitted" / "data_docs" / "local_site" / "index.html"
    if docs_path.exists():
        print(f"\n  📊 Data Docs: file://{docs_path}")

    return success


def _print_summary(run_id, evaluated, successful, failed, passed):
    status = "✅ PASSED" if passed else "❌ FAILED"
    print(
        f"\n{'='*55}\n"
        f"  GX SILVER CHECKPOINT — {status}\n"
        f"  Run       : {run_id}\n"
        f"  Evaluated : {evaluated}\n"
        f"  Passed    : {successful}\n"
        f"  Failed    : {failed}\n"
        f"{'='*55}"
    )


def rebuild_suite() -> None:
    """Force-rebuild the silver expectation suite."""
    if not GX_AVAILABLE:
        raise ImportError("great_expectations is not installed.")
    context = _get_context()
    _build_silver_suite(context)
    print(f"✅ Suite '{SUITE_NAME}' rebuilt in {GX_ROOT}")


if __name__ == "__main__":
    import sys
    import glob
    files = sorted(
        glob.glob(str(PROJECT_ROOT / "data" / "silver" / "avito_clean_*.csv")))
    if not files:
        print("No silver CSV files found.")
        sys.exit(1)
    df = pd.read_csv(files[-1])
    passed = run_silver_checkpoint(df)
    sys.exit(0 if passed else 1)
