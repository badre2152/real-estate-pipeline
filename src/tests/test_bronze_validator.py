"""
tests/test_bronze_validator.py
================================
Unit tests for src/staging/bronze_validator.py

Coverage:
  - All 4 HARD rules (min_records, required_keys, prix_fill, ville_fill)
  - All 8 SOFT rules (no failures expected, just log warnings)
  - validate_bronze() summary dict structure
  - Edge cases: empty list, single record, all-artefact villes
"""

from src.staging.bronze_validator import (
    validate_bronze,
    BronzeValidationError,
    _rule_min_records,
    _rule_required_keys,
    _rule_prix_fill_rate,
    _rule_ville_fill_rate,
    _rule_prix_format,
    _rule_prix_type_values,
    _rule_lien_format,
    _rule_lien_uniqueness,
    _rule_scraped_at_format,
    _rule_artefact_villes,
    HARD_MIN_RECORDS,
)
from tests.conftest import make_bronze_record, make_bronze_records
import sys
import os
import unittest

# ── Path setup ──────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ══════════════════════════════════════════════════════════════════════════════
# HARD RULE: min_records
# ══════════════════════════════════════════════════════════════════════════════

class TestRuleMinRecords(unittest.TestCase):

    def test_passes_with_enough_records(self):
        records = make_bronze_records(HARD_MIN_RECORDS)
        # Should not raise
        _rule_min_records(records)

    def test_passes_with_many_records(self):
        _rule_min_records(make_bronze_records(50))

    def test_fails_with_zero_records(self):
        with self.assertRaises(BronzeValidationError) as ctx:
            _rule_min_records([])
        self.assertIn("0 records", str(ctx.exception))

    def test_fails_with_one_record(self):
        with self.assertRaises(BronzeValidationError):
            _rule_min_records([make_bronze_record()])

    def test_fails_with_exactly_one_below_minimum(self):
        records = make_bronze_records(HARD_MIN_RECORDS - 1)
        with self.assertRaises(BronzeValidationError):
            _rule_min_records(records)

    def test_passes_at_exact_minimum(self):
        records = make_bronze_records(HARD_MIN_RECORDS)
        _rule_min_records(records)  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# HARD RULE: required_keys
# ══════════════════════════════════════════════════════════════════════════════

class TestRuleRequiredKeys(unittest.TestCase):

    def test_passes_with_all_keys(self):
        _rule_required_keys(make_bronze_records(5))

    def test_fails_when_prix_missing(self):
        records = make_bronze_records(5)
        for r in records:
            del r["prix"]
        with self.assertRaises(BronzeValidationError) as ctx:
            _rule_required_keys(records)
        self.assertIn("prix", str(ctx.exception))

    def test_fails_when_ville_missing(self):
        records = make_bronze_records(5)
        for r in records:
            del r["ville"]
        with self.assertRaises(BronzeValidationError):
            _rule_required_keys(records)

    def test_fails_when_lien_missing(self):
        records = make_bronze_records(5)
        for r in records:
            del r["lien"]
        with self.assertRaises(BronzeValidationError):
            _rule_required_keys(records)

    def test_fails_when_scraped_at_missing(self):
        records = make_bronze_records(5)
        for r in records:
            del r["scraped_at"]
        with self.assertRaises(BronzeValidationError):
            _rule_required_keys(records)

    def test_fails_on_partial_records(self):
        """Only some records missing keys should still fail."""
        records = make_bronze_records(5)
        del records[2]["prix"]
        with self.assertRaises(BronzeValidationError):
            _rule_required_keys(records)

    def test_extra_keys_are_allowed(self):
        """Extra columns in a record must not cause failure."""
        records = make_bronze_records(5)
        for r in records:
            r["extra_field"] = "ok"
        _rule_required_keys(records)  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# HARD RULE: prix_fill_rate
# ══════════════════════════════════════════════════════════════════════════════

class TestRulePrixFillRate(unittest.TestCase):

    def test_passes_with_all_prix_filled(self):
        _rule_prix_fill_rate(make_bronze_records(10))

    def test_passes_at_exactly_50_pct(self):
        records = make_bronze_records(10)
        for r in records[:5]:
            r["prix"] = None
        _rule_prix_fill_rate(records)  # 50% = exactly at threshold

    def test_fails_when_all_prix_null(self):
        records = make_bronze_records(10)
        for r in records:
            r["prix"] = None
        with self.assertRaises(BronzeValidationError):
            _rule_prix_fill_rate(records)

    def test_fails_when_prix_empty_string(self):
        records = make_bronze_records(10)
        for r in records:
            r["prix"] = ""
        with self.assertRaises(BronzeValidationError):
            _rule_prix_fill_rate(records)

    def test_fails_below_50_pct(self):
        records = make_bronze_records(10)
        for r in records[:6]:   # 60% null → 40% filled → below threshold
            r["prix"] = None
        with self.assertRaises(BronzeValidationError):
            _rule_prix_fill_rate(records)


# ══════════════════════════════════════════════════════════════════════════════
# HARD RULE: ville_fill_rate
# ══════════════════════════════════════════════════════════════════════════════

class TestRuleVilleFillRate(unittest.TestCase):

    def test_passes_with_all_villes_filled(self):
        _rule_ville_fill_rate(make_bronze_records(10))

    def test_fails_when_all_villes_null(self):
        records = make_bronze_records(10)
        for r in records:
            r["ville"] = None
        with self.assertRaises(BronzeValidationError):
            _rule_ville_fill_rate(records)

    def test_fails_when_all_villes_are_artefacts(self):
        """Artefact villes count as missing in the fill-rate calculation."""
        records = make_bronze_records(10)
        for r in records:
            r["ville"] = "COURS ET FORMATIONS"
        with self.assertRaises(BronzeValidationError):
            _rule_ville_fill_rate(records)

    def test_fails_below_50_pct_real_villes(self):
        records = make_bronze_records(10)
        for r in records[:6]:
            r["ville"] = None
        with self.assertRaises(BronzeValidationError):
            _rule_ville_fill_rate(records)

    def test_passes_when_artefacts_mixed_with_enough_real_villes(self):
        """3 artefacts + 7 real = 70% real → should pass."""
        records = make_bronze_records(10)
        for r in records[:3]:
            r["ville"] = "COURS ET FORMATIONS"
        _rule_ville_fill_rate(records)  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# SOFT RULES (no exception expected — just logging)
# ══════════════════════════════════════════════════════════════════════════════

class TestSoftRules(unittest.TestCase):

    def test_prix_format_valid(self):
        """Valid DH formats must not raise."""
        records = [
            make_bronze_record(prix="4 500 DH"),
            make_bronze_record(prix="4\u202f500 DH"),
            make_bronze_record(prix="12500"),
            make_bronze_record(prix="12500DH"),
        ]
        _rule_prix_format(records)   # must not raise

    def test_prix_format_invalid_does_not_raise(self):
        """Invalid format is soft — must WARN but not raise."""
        records = make_bronze_records(5)
        records[0]["prix"] = "contact us"
        records[1]["prix"] = "N/A"
        _rule_prix_format(records)   # must not raise

    def test_prix_type_valid_values(self):
        for pt in ["mensuel", "journalier", "inconnu"]:
            records = make_bronze_records(5)
            for r in records:
                r["prix_type"] = pt
            _rule_prix_type_values(records)  # must not raise

    def test_prix_type_invalid_does_not_raise(self):
        records = make_bronze_records(5)
        records[0]["prix_type"] = "hebdomadaire"
        _rule_prix_type_values(records)  # soft — must not raise

    def test_lien_format_valid(self):
        _rule_lien_format(make_bronze_records(5))  # all valid by default

    def test_lien_format_invalid_does_not_raise(self):
        records = make_bronze_records(5)
        records[0]["lien"] = "https://www.mubawab.ma/fr/something"
        _rule_lien_format(records)  # soft — must not raise

    def test_lien_uniqueness_all_unique(self):
        _rule_lien_uniqueness(make_bronze_records(5))  # must not raise

    def test_lien_uniqueness_with_duplicates_does_not_raise(self):
        records = make_bronze_records(5)
        records[1]["lien"] = records[0]["lien"]   # introduce duplicate
        _rule_lien_uniqueness(records)   # soft — must not raise

    def test_scraped_at_valid_format(self):
        records = make_bronze_records(5)
        _rule_scraped_at_format(records)  # must not raise

    def test_scraped_at_future_does_not_raise(self):
        records = make_bronze_records(5)
        records[0]["scraped_at"] = "2099-01-01T00:00:00"
        _rule_scraped_at_format(records)  # soft — must not raise

    def test_artefact_villes_clean(self):
        _rule_artefact_villes(make_bronze_records(5))  # must not raise

    def test_artefact_villes_present_does_not_raise(self):
        records = make_bronze_records(5)
        records[0]["ville"] = "COURS ET FORMATIONS"
        _rule_artefact_villes(records)   # soft — must not raise


# ══════════════════════════════════════════════════════════════════════════════
# validate_bronze() — integration tests
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateBronzeIntegration(unittest.TestCase):

    def test_passes_with_clean_records(self):
        records = make_bronze_records(15)
        summary = validate_bronze(records)
        self.assertEqual(summary["hard_failures"], 0)
        self.assertEqual(summary["total_records"], 15)

    def test_summary_keys_present(self):
        summary = validate_bronze(make_bronze_records(10))
        expected_keys = {"total_records", "valid_records", "error_records",
                         "hard_failures", "soft_warnings"}
        self.assertEqual(set(summary.keys()), expected_keys)

    def test_aborts_on_too_few_records(self):
        with self.assertRaises(BronzeValidationError):
            validate_bronze(make_bronze_records(2))

    def test_aborts_on_missing_required_key(self):
        records = make_bronze_records(10)
        for r in records:
            del r["lien"]
        with self.assertRaises(BronzeValidationError):
            validate_bronze(records)

    def test_aborts_on_empty_prix(self):
        records = make_bronze_records(10)
        for r in records:
            r["prix"] = None
        with self.assertRaises(BronzeValidationError):
            validate_bronze(records)

    def test_aborts_on_empty_ville(self):
        records = make_bronze_records(10)
        for r in records:
            r["ville"] = None
        with self.assertRaises(BronzeValidationError):
            validate_bronze(records)

    def test_counts_error_records_in_summary(self):
        records = make_bronze_records(10)
        records[0]["error"] = "timeout"
        records[1]["error"] = "parse_failed"
        summary = validate_bronze(records)
        self.assertEqual(summary["error_records"], 2)
        self.assertEqual(summary["valid_records"], 8)

    def test_real_bronze_file(self):
        """Smoke test: run against the actual bronze fixture file."""
        import json
        import glob
        bronze_files = sorted(
            glob.glob(
                os.path.join(
                    PROJECT_ROOT,
                    "data",
                    "bronze",
                    "avito_raw_*.json")))
        if not bronze_files:
            self.skipTest("No bronze fixture file found")
        with open(bronze_files[-1], encoding="utf-8") as f:
            records = json.load(f)
        summary = validate_bronze(records)
        self.assertEqual(summary["hard_failures"], 0)
        self.assertGreater(summary["total_records"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
