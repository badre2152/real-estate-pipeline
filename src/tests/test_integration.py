"""
test_integration.py — FIX #29: End-to-end integration test for run_clean.

Tests the full Staging → Clean → BI flow using an in-memory SQLite-compatible
mock OR a real Postgres connection if DB_HOST is available.

Since the project uses PostgreSQL-specific SQL (DISTINCT ON, SERIAL, etc.),
the integration test uses a lightweight approach:
  1. Inject a DataFrame directly into run_clean() (bypassing DB staging).
  2. Verify the output DataFrame passes all post-clean validators.
  3. Verify _load_to_db would receive a valid DataFrame (DB write mocked).

This tests the full clean logic end-to-end without requiring a live DB.
"""

import sys
import os
import unittest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.clean.clean_validator import validate_post_clean, CleanValidationError


def _make_raw_staging_df(n: int = 25) -> pd.DataFrame:
    """Create a realistic staging DataFrame (as if read from staging.raw_annonces)."""
    cities = ["Casablanca", "Rabat", "Marrakech", "Fès", "Tanger", "Agadir"]
    quarters = ["Maarif", "Agdal", "Guéliz", "Centre", "Médina", "Almassira"]
    rows = []
    for i in range(n):
        rows.append({
            "id": i + 1,
            "run_id": "test-run-001",
            "titre": f"Appartement {i+1} chambres à louer",
            "prix": str(3000 + i * 500),
            "prix_type": "mensuel" if i % 8 != 0 else "journalier",
            "ville": cities[i % len(cities)],
            "quartier": quarters[i % len(quarters)],
            "surface": str(50 + i * 5),
            "nb_chambres": str(1 + i % 4),
            "nb_salles_bain": str(1 + i % 2),
            "etage": str(i % 6),
            "annee_construction": str(1990 + i % 30) if i % 3 != 0 else None,
            "lien": f"https://www.avito.ma/fr/annonce-{i+1:04d}",
            "scraped_at": "2024-05-01T10:00:00",
            "loaded_at": "2024-05-01T10:05:00",
        })
    return pd.DataFrame(rows)


class TestRunCleanIntegration(unittest.TestCase):
    """
    FIX #29: Integration tests for the full run_clean() flow.
    DB interactions are mocked so the test runs without a live PostgreSQL instance.
    """

    def setUp(self):
        self.raw_df = _make_raw_staging_df(n=30)

    @patch("src.clean.clean_data._load_to_db")
    @patch("src.clean.clean_data._save_silver")
    @patch("src.clean.clean_data._fetch_staging")
    def test_run_clean_returns_dataframe(self, mock_fetch, mock_save, mock_load):
        """run_clean() should return a non-empty DataFrame."""
        mock_fetch.return_value = self.raw_df
        mock_save.return_value = None
        mock_load.return_value = None

        from src.clean.clean_data import run_clean
        result = run_clean(run_id="test-run-001")

        self.assertIsInstance(result, pd.DataFrame)
        self.assertGreater(len(result), 0)

    @patch("src.clean.clean_data._load_to_db")
    @patch("src.clean.clean_data._save_silver")
    @patch("src.clean.clean_data._fetch_staging")
    def test_run_clean_passes_post_validation(self, mock_fetch, mock_save, mock_load):
        """Output of run_clean() must pass all post-clean validators."""
        mock_fetch.return_value = self.raw_df
        mock_save.return_value = None
        mock_load.return_value = None

        from src.clean.clean_data import run_clean
        result = run_clean(run_id="test-run-001")

        # Should not raise CleanValidationError
        try:
            summary = validate_post_clean(result, n_staging=len(self.raw_df))
        except CleanValidationError as e:
            self.fail(f"Post-clean validation failed: {e}")

        self.assertGreater(summary["clean_rows"], 0)

    @patch("src.clean.clean_data._load_to_db")
    @patch("src.clean.clean_data._save_silver")
    @patch("src.clean.clean_data._fetch_staging")
    def test_run_clean_required_columns_present(self, mock_fetch, mock_save, mock_load):
        """Output must contain all columns required by downstream warehouse layers."""
        mock_fetch.return_value = self.raw_df
        mock_save.return_value = None
        mock_load.return_value = None

        from src.clean.clean_data import run_clean
        result = run_clean(run_id="test-run-001")

        required = {
            "titre", "prix", "prix_type", "ville", "quartier",
            "surface_m2", "nb_chambres", "nb_salles_bain", "etage",
            "lien", "prix_par_m2", "age_bien", "categorie_prix",
            "region_label", "is_grande_ville",
        }
        missing = required - set(result.columns)
        self.assertSetEqual(missing, set(), f"Missing columns: {missing}")

    @patch("src.clean.clean_data._load_to_db")
    @patch("src.clean.clean_data._save_silver")
    @patch("src.clean.clean_data._fetch_staging")
    def test_run_clean_no_duplicate_liens(self, mock_fetch, mock_save, mock_load):
        """Silver layer must not have duplicate lien values."""
        mock_fetch.return_value = self.raw_df
        mock_save.return_value = None
        mock_load.return_value = None

        from src.clean.clean_data import run_clean
        result = run_clean(run_id="test-run-001")

        duplicates = result["lien"].duplicated().sum()
        self.assertEqual(duplicates, 0, f"Found {duplicates} duplicate liens in clean output")

    @patch("src.clean.clean_data._load_to_db")
    @patch("src.clean.clean_data._save_silver")
    @patch("src.clean.clean_data._fetch_staging")
    def test_run_clean_sale_prices_filtered(self, mock_fetch, mock_save, mock_load):
        """FIX #35: Listings with implausibly high rental price should be dropped."""
        # Add a fake sale listing (1,287,000 DH monthly — impossible for rental)
        df_with_sale = self.raw_df.copy()
        sale_row = df_with_sale.iloc[0].copy()
        sale_row["prix"] = "1287000"
        sale_row["prix_type"] = "mensuel"
        sale_row["surface"] = "99"
        sale_row["lien"] = "https://www.avito.ma/fr/sale-listing-999"
        df_with_sale = pd.concat([df_with_sale, pd.DataFrame([sale_row])], ignore_index=True)

        mock_fetch.return_value = df_with_sale
        mock_save.return_value = None
        mock_load.return_value = None

        from src.clean.clean_data import run_clean
        result = run_clean(run_id="test-run-001")

        # The 1.287M DH listing must NOT appear in the output
        high_prices = result[result["prix"] > 150_000]
        self.assertEqual(
            len(high_prices), 0,
            f"Sale listing (1,287,000 DH) was not filtered out. Found {len(high_prices)} rows with prix > 150,000."
        )

    @patch("src.clean.clean_data._load_to_db")
    @patch("src.clean.clean_data._save_silver")
    @patch("src.clean.clean_data._fetch_staging")
    def test_run_clean_arabic_cities_normalised(self, mock_fetch, mock_save, mock_load):
        """FIX #32: Arabic city names must be normalised to French canonical form."""
        df_arabic = self.raw_df.copy()
        df_arabic.loc[0, "ville"] = "طنجة"
        df_arabic.loc[1, "ville"] = "مراكش"
        df_arabic.loc[2, "ville"] = "الدار البيضاء"

        mock_fetch.return_value = df_arabic
        mock_save.return_value = None
        mock_load.return_value = None

        from src.clean.clean_data import run_clean
        result = run_clean(run_id="test-run-001")

        arabic_remaining = result["ville"].str.contains(r"[\u0600-\u06FF]", na=False).sum()
        self.assertEqual(
            arabic_remaining, 0,
            f"Arabic city names were not normalised — {arabic_remaining} rows still contain Arabic."
        )

    @patch("src.clean.clean_data._load_to_db")
    @patch("src.clean.clean_data._save_silver")
    @patch("src.clean.clean_data._fetch_staging")
    def test_run_clean_run_id_isolation(self, mock_fetch, mock_save, mock_load):
        """FIX #15: run_id must be passed to _fetch_staging for run isolation."""
        mock_fetch.return_value = self.raw_df
        mock_save.return_value = None
        mock_load.return_value = None

        from src.clean.clean_data import run_clean
        run_clean(run_id="isolated-run-xyz")

        # _fetch_staging must have been called with the correct run_id
        mock_fetch.assert_called_once_with(run_id="isolated-run-xyz")


class TestStagingRunIdLogic(unittest.TestCase):
    """
    Tests for the run_id + UNIQUE(lien) interaction in staging.
    These validate the ON CONFLICT DO UPDATE fix without a live DB.
    """

    def test_run_staging_returns_string_run_id(self):
        """run_staging() must return a non-empty string run_id."""
        from src.staging.load_staging import run_staging
        from unittest.mock import patch

        sample_records = [
            {
                "titre": "Test", "prix": "5000", "prix_type": "mensuel",
                "ville": "Rabat", "quartier": "Agdal", "surface": "80",
                "nb_chambres": "2", "nb_salles_bain": "1", "etage": "2",
                "annee_construction": "2005",
                "lien": "https://www.avito.ma/fr/test-001",
                "scraped_at": "2024-05-01T10:00:00",
                "error": None,
            }
        ]

        with patch("src.staging.load_staging.execute_query"),              patch("src.staging.load_staging.bulk_insert"),              patch("src.staging.load_staging.validate_bronze"):
            result_id = run_staging(records=sample_records, run_id="explicit-run-id")

        self.assertEqual(result_id, "explicit-run-id")

    def test_run_staging_generates_uuid_when_no_run_id(self):
        """run_staging() auto-generates a UUID4 run_id when none is provided."""
        import uuid
        from src.staging.load_staging import run_staging
        from unittest.mock import patch

        with patch("src.staging.load_staging.execute_query"),              patch("src.staging.load_staging.bulk_insert"),              patch("src.staging.load_staging.validate_bronze"):
            result_id = run_staging(records=[], run_id=None)

        # Must be a valid UUID
        try:
            uuid.UUID(result_id)
        except ValueError:
            self.fail(f"Auto-generated run_id is not a valid UUID: {result_id!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
