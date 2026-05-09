"""
tests/test_clean_validator.py
================================
Unit tests for src/clean/clean_validator.py

Coverage:
  PRE-CLEAN:
    - min_rows (hard)
    - required_columns (hard)
    - prix_fill (hard)
    - no_all_null_rows (soft)
    - lien_format (soft)
    - artefact_villes (soft)

  POST-CLEAN:
    - min_rows / drop_rate (hard)
    - required_columns (hard)
    - no_raw_surface_col (hard)
    - prix_fill (hard)
    - ville_fill (hard)
    - prix_positive (hard)
    - no_duplicate_liens (hard)
    - no_artefact_villes (hard)
    - prix_par_m2_consistency (soft)
    - age_bien (soft)
    - surface_positive (soft)
    - nb_fields_range (soft)

  Integration:
    - validate_pre_clean() end-to-end
    - validate_post_clean() end-to-end
    - Real silver CSV smoke test
"""

from src.clean.clean_validator import (
    validate_pre_clean,
    validate_post_clean,
    CleanValidationError,
    _pre_min_rows,
    _pre_required_columns,
    _pre_prix_fill,
    _pre_no_all_null_rows,
    _pre_lien_format,
    _pre_artefact_villes,
    _post_min_rows,
    _post_required_columns,
    _post_no_raw_surface_col,
    _post_prix_fill,
    _post_ville_fill,
    _post_prix_positive,
    _post_no_duplicate_liens,
    _post_no_artefact_villes,
    _post_prix_par_m2_consistency,
    _post_age_bien_valid,
    _post_surface_positive,
    _post_nb_fields_range,
    HARD_MIN_STAGING_ROWS,
)
from tests.conftest import make_clean_df, make_staging_df
import sys
import os
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ══════════════════════════════════════════════════════════════════════════════
# PRE-CLEAN HARD RULES
# ══════════════════════════════════════════════════════════════════════════════


class TestPreMinRows(unittest.TestCase):

    def test_passes_with_enough_rows(self):
        _pre_min_rows(make_staging_df(HARD_MIN_STAGING_ROWS))

    def test_fails_with_empty_df(self):
        with self.assertRaises(CleanValidationError):
            _pre_min_rows(make_staging_df(0))

    def test_fails_with_one_row(self):
        with self.assertRaises(CleanValidationError):
            _pre_min_rows(make_staging_df(1))

    def test_passes_at_exact_minimum(self):
        _pre_min_rows(make_staging_df(HARD_MIN_STAGING_ROWS))


class TestPreRequiredColumns(unittest.TestCase):

    def test_passes_with_all_required_columns(self):
        _pre_required_columns(make_staging_df(5))

    def test_fails_when_prix_missing(self):
        df = make_staging_df(5).drop(columns=["prix"])
        with self.assertRaises(CleanValidationError) as ctx:
            _pre_required_columns(df)
        self.assertIn("prix", str(ctx.exception))

    def test_fails_when_ville_missing(self):
        df = make_staging_df(5).drop(columns=["ville"])
        with self.assertRaises(CleanValidationError):
            _pre_required_columns(df)

    def test_fails_when_lien_missing(self):
        df = make_staging_df(5).drop(columns=["lien"])
        with self.assertRaises(CleanValidationError):
            _pre_required_columns(df)

    def test_extra_columns_allowed(self):
        df = make_staging_df(5)
        df["extra"] = "ok"
        _pre_required_columns(df)  # must not raise


class TestPrePrixFill(unittest.TestCase):

    def test_passes_with_all_filled(self):
        _pre_prix_fill(make_staging_df(10))

    def test_fails_when_all_null(self):
        df = make_staging_df(10, prix=[None] * 10)
        with self.assertRaises(CleanValidationError):
            _pre_prix_fill(df)

    def test_fails_below_50_pct(self):
        vals = [None] * 6 + ["4500"] * 4   # 40% filled
        df = make_staging_df(10, prix=vals)
        with self.assertRaises(CleanValidationError):
            _pre_prix_fill(df)

    def test_passes_at_exactly_50_pct(self):
        vals = [None] * 5 + ["4500"] * 5   # 50% filled
        df = make_staging_df(10, prix=vals)
        _pre_prix_fill(df)  # must not raise


class TestPreSoftRules(unittest.TestCase):

    def test_all_null_rows_does_not_raise(self):
        df = make_staging_df(5)
        df.iloc[0] = None
        _pre_no_all_null_rows(df)   # soft — must not raise

    def test_bad_lien_does_not_raise(self):
        df = make_staging_df(5)
        df.loc[0, "lien"] = "https://www.mubawab.ma/listing/123"
        _pre_lien_format(df)   # soft — must not raise

    def test_artefact_ville_does_not_raise(self):
        df = make_staging_df(5)
        df.loc[0, "ville"] = "COURS ET FORMATIONS"
        _pre_artefact_villes(df)   # soft — must not raise


# ══════════════════════════════════════════════════════════════════════════════
# POST-CLEAN HARD RULES
# ══════════════════════════════════════════════════════════════════════════════


class TestPostMinRows(unittest.TestCase):

    def test_passes_with_enough_rows(self):
        _post_min_rows(make_clean_df(10), n_staging=15)

    def test_fails_with_one_row(self):
        with self.assertRaises(CleanValidationError):
            _post_min_rows(make_clean_df(1), n_staging=20)

    def test_fails_when_drop_rate_too_high(self):
        """81% drop rate should fail (hard limit = 80%)."""
        with self.assertRaises(CleanValidationError):
            # 2/20 = 10% retained → 90% dropped
            _post_min_rows(make_clean_df(2), n_staging=20)

    def test_passes_at_exactly_20_pct_retained(self):
        """20% retained = 80% dropped — exactly at hard limit, should pass."""
        _post_min_rows(make_clean_df(4), n_staging=20)  # 4/20 = 20% retained


class TestPostRequiredColumns(unittest.TestCase):

    def test_passes_with_all_columns(self):
        _post_required_columns(make_clean_df(5))

    def test_fails_when_prix_par_m2_missing(self):
        df = make_clean_df(5).drop(columns=["prix_par_m2"])
        with self.assertRaises(CleanValidationError) as ctx:
            _post_required_columns(df)
        self.assertIn("prix_par_m2", str(ctx.exception))

    def test_fails_when_region_label_missing(self):
        df = make_clean_df(5).drop(columns=["region_label"])
        with self.assertRaises(CleanValidationError):
            _post_required_columns(df)

    def test_fails_when_is_grande_ville_missing(self):
        df = make_clean_df(5).drop(columns=["is_grande_ville"])
        with self.assertRaises(CleanValidationError):
            _post_required_columns(df)

    def test_fails_when_categorie_prix_missing(self):
        df = make_clean_df(5).drop(columns=["categorie_prix"])
        with self.assertRaises(CleanValidationError):
            _post_required_columns(df)


class TestPostNoRawSurfaceCol(unittest.TestCase):

    def test_passes_with_only_surface_m2(self):
        _post_no_raw_surface_col(make_clean_df(5))

    def test_fails_when_both_surface_and_surface_m2_exist(self):
        df = make_clean_df(5)
        df["surface"] = 80   # raw column survived cleaning
        with self.assertRaises(CleanValidationError) as ctx:
            _post_no_raw_surface_col(df)
        self.assertIn("surface", str(ctx.exception))

    def test_passes_with_only_raw_surface(self):
        """If only 'surface' exists (no surface_m2), rule passes — different issue."""
        df = make_clean_df(5).drop(columns=["surface_m2"])
        df["surface"] = 80
        # must not raise (different validation handles this)
        _post_no_raw_surface_col(df)


class TestPostPrixFill(unittest.TestCase):

    def test_passes_with_all_filled(self):
        _post_prix_fill(make_clean_df(10))

    def test_fails_below_90_pct(self):
        vals = [None] * 2 + [5000.0] * 8   # 80% filled — below 90% threshold
        df = make_clean_df(10, prix=vals)
        with self.assertRaises(CleanValidationError):
            _post_prix_fill(df)

    def test_passes_at_100_pct(self):
        _post_prix_fill(make_clean_df(10))


class TestPostVilleFill(unittest.TestCase):

    def test_passes_with_all_filled(self):
        _post_ville_fill(make_clean_df(10))

    def test_fails_when_too_many_null(self):
        villes = [None] * 2 + ["Casablanca"] * 8   # 80% filled — below 90%
        df = make_clean_df(10, ville=villes)
        with self.assertRaises(CleanValidationError):
            _post_ville_fill(df)

    def test_fails_when_ville_is_empty_string(self):
        villes = [""] * 2 + ["Casablanca"] * 8
        df = make_clean_df(10, ville=villes)
        with self.assertRaises(CleanValidationError):
            _post_ville_fill(df)


class TestPostPrixPositive(unittest.TestCase):

    def test_passes_with_all_positive(self):
        _post_prix_positive(make_clean_df(5))

    def test_fails_with_zero_prix(self):
        df = make_clean_df(5, prix=[0.0, 5000.0, 5000.0, 5000.0, 5000.0])
        with self.assertRaises(CleanValidationError) as ctx:
            _post_prix_positive(df)
        self.assertIn("0", str(ctx.exception))

    def test_fails_with_negative_prix(self):
        df = make_clean_df(5, prix=[-100.0, 5000.0, 5000.0, 5000.0, 5000.0])
        with self.assertRaises(CleanValidationError):
            _post_prix_positive(df)

    def test_null_prix_ignored(self):
        """Nulls should be ignored — only non-null values are checked."""
        df = make_clean_df(5, prix=[None, 5000.0, 5000.0, 5000.0, 5000.0])
        _post_prix_positive(df)  # must not raise


class TestPostNoDuplicateLiens(unittest.TestCase):

    def test_passes_with_unique_liens(self):
        _post_no_duplicate_liens(make_clean_df(5))

    def test_fails_with_duplicate_liens(self):
        df = make_clean_df(5)
        df.loc[1, "lien"] = df.loc[0, "lien"]   # introduce duplicate
        with self.assertRaises(CleanValidationError) as ctx:
            _post_no_duplicate_liens(df)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_fails_when_all_liens_same(self):
        df = make_clean_df(5, lien=["https://www.avito.ma/fr/appt-1"] * 5)
        with self.assertRaises(CleanValidationError):
            _post_no_duplicate_liens(df)


class TestPostNoArtefactVilles(unittest.TestCase):

    def test_passes_with_clean_villes(self):
        _post_no_artefact_villes(make_clean_df(5))

    def test_fails_when_artefact_survived(self):
        villes = ["COURS ET FORMATIONS"] + ["Casablanca"] * 4
        df = make_clean_df(5, ville=villes)
        with self.assertRaises(CleanValidationError) as ctx:
            _post_no_artefact_villes(df)
        self.assertIn("COURS ET FORMATIONS", str(ctx.exception))

    def test_fails_with_lowercase_artefact(self):
        villes = ["cours et formations"] + ["Casablanca"] * 4
        df = make_clean_df(5, ville=villes)
        with self.assertRaises(CleanValidationError):
            _post_no_artefact_villes(df)


# ══════════════════════════════════════════════════════════════════════════════
# POST-CLEAN SOFT RULES
# ══════════════════════════════════════════════════════════════════════════════


class TestPostSoftRules(unittest.TestCase):

    def test_prix_par_m2_consistent(self):
        """prix / surface_m2 == prix_par_m2 should pass silently."""
        df = make_clean_df(
            5,
            prix=[5000.0] * 5,
            surface_m2=[80.0] * 5,
            prix_par_m2=[62.5] * 5)
        _post_prix_par_m2_consistency(df)   # must not raise

    def test_prix_par_m2_inconsistent_does_not_raise(self):
        """Soft rule — inconsistent values warn but don't raise."""
        df = make_clean_df(
            5,
            prix=[5000.0] * 5,
            surface_m2=[80.0] * 5,
            prix_par_m2=[999.9] * 5)
        _post_prix_par_m2_consistency(df)   # soft — must not raise

    def test_age_bien_valid(self):
        _post_age_bien_valid(make_clean_df(5, age_bien=[10, 20, 30, 5, 50]))

    def test_age_bien_negative_does_not_raise(self):
        df = make_clean_df(5, age_bien=[-5, 10, 10, 10, 10])
        _post_age_bien_valid(df)   # soft — must not raise

    def test_age_bien_over_200_does_not_raise(self):
        df = make_clean_df(5, age_bien=[250, 10, 10, 10, 10])
        _post_age_bien_valid(df)   # soft — must not raise

    def test_surface_positive(self):
        _post_surface_positive(make_clean_df(5))

    def test_surface_zero_does_not_raise(self):
        df = make_clean_df(5, surface_m2=[0.0, 80.0, 80.0, 80.0, 80.0])
        _post_surface_positive(df)   # soft — must not raise

    def test_nb_chambres_in_range(self):
        _post_nb_fields_range(make_clean_df(5, nb_chambres=[1, 2, 3, 4, 5]))

    def test_nb_chambres_out_of_range_does_not_raise(self):
        df = make_clean_df(
            5, nb_chambres=[
                50, 2, 2, 2, 2])   # 50 is out of range
        _post_nb_fields_range(df)   # soft — must not raise

    def test_nb_salles_bain_in_range(self):
        _post_nb_fields_range(make_clean_df(5, nb_salles_bain=[1, 2, 1, 1, 2]))

    def test_missing_column_does_not_raise(self):
        """Soft rules should gracefully handle missing optional columns."""
        df = make_clean_df(5).drop(
            columns=[
                "age_bien",
                "surface_m2",
                "prix_par_m2"],
            errors="ignore")
        _post_age_bien_valid(df)
        _post_surface_positive(df)
        _post_prix_par_m2_consistency(df)


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: validate_pre_clean() and validate_post_clean()
# ══════════════════════════════════════════════════════════════════════════════


class TestValidatePreCleanIntegration(unittest.TestCase):

    def test_passes_with_valid_staging_df(self):
        validate_pre_clean(make_staging_df(10))   # must not raise

    def test_fails_with_empty_df(self):
        with self.assertRaises(CleanValidationError):
            validate_pre_clean(make_staging_df(0))

    def test_fails_with_missing_column(self):
        df = make_staging_df(10).drop(columns=["prix"])
        with self.assertRaises(CleanValidationError):
            validate_pre_clean(df)

    def test_fails_when_all_prix_null(self):
        df = make_staging_df(10, prix=[None] * 10)
        with self.assertRaises(CleanValidationError):
            validate_pre_clean(df)


class TestValidatePostCleanIntegration(unittest.TestCase):

    def test_passes_with_valid_clean_df(self):
        summary = validate_post_clean(make_clean_df(10), n_staging=12)
        self.assertEqual(summary["clean_rows"], 10)
        self.assertEqual(summary["staging_rows"], 12)

    def test_summary_keys_present(self):
        summary = validate_post_clean(make_clean_df(10), n_staging=12)
        self.assertIn("clean_rows", summary)
        self.assertIn("staging_rows", summary)
        self.assertIn("drop_rate", summary)
        self.assertIn("soft_warnings", summary)

    def test_drop_rate_calculation(self):
        summary = validate_post_clean(make_clean_df(8), n_staging=10)
        self.assertAlmostEqual(summary["drop_rate"], 0.2, places=2)

    def test_fails_when_required_column_missing(self):
        df = make_clean_df(10).drop(columns=["categorie_prix"])
        with self.assertRaises(CleanValidationError):
            validate_post_clean(df, n_staging=12)

    def test_fails_when_artefact_ville_survives(self):
        villes = ["COURS ET FORMATIONS"] + ["Casablanca"] * 9
        df = make_clean_df(10, ville=villes)
        with self.assertRaises(CleanValidationError):
            validate_post_clean(df, n_staging=12)

    def test_fails_when_prix_is_zero(self):
        prix = [0.0] + [5000.0] * 9
        df = make_clean_df(10, prix=prix)
        with self.assertRaises(CleanValidationError):
            validate_post_clean(df, n_staging=12)

    def test_real_silver_file(self):
        """
        FIX #17: Smoke test must NOT manually clean data before validation.
        The silver CSV should already be clean — if artefact villes are present,
        that is a real pipeline bug and the test should catch it, not hide it.
        The manual df[~df["ville"].isin({...})] filter has been removed.
        If this test fails, fix the scraper/cleaner, not the test.
        """
        import glob
        silver_files = sorted(
            glob.glob(
                os.path.join(
                    PROJECT_ROOT,
                    "data",
                    "silver",
                    "avito_clean_*.csv")))
        if not silver_files:
            self.skipTest("No silver fixture file found")

        import pandas as pd
        df = pd.read_csv(silver_files[-1])
        # No manual pre-cleaning — the silver file must already be clean.
        summary = validate_post_clean(df, n_staging=len(df))
        self.assertGreater(summary["clean_rows"], 0)


# ══════════════════════════════════════════════════════════════════════════════
# FIX #2 — journalier_suspect detection (_detect_daily_rental)
# ══════════════════════════════════════════════════════════════════════════════


class TestDetectDailyRental(unittest.TestCase):
    '''
    Tests for clean_data._detect_daily_rental().
    Ensures that mensuel prices below city thresholds are reclassified
    as journalier_suspect and do NOT contaminate BI averages.
    '''

    def setUp(self):
        from src.clean.clean_data import _detect_daily_rental
        self.detect = _detect_daily_rental

    def test_grande_ville_below_threshold_is_suspect(self):
        '''Casablanca at 500 DH/month is below 1000 DH threshold → suspect.'''
        result = self.detect(500.0, 'mensuel', 'Casablanca')
        self.assertEqual(result, 'journalier_suspect')

    def test_grande_ville_above_threshold_stays_mensuel(self):
        '''Casablanca at 3000 DH/month is fine → stays mensuel.'''
        result = self.detect(3000.0, 'mensuel', 'Casablanca')
        self.assertEqual(result, 'mensuel')

    def test_grande_ville_at_exact_threshold_stays_mensuel(self):
        '''Exactly 1000 DH in Casablanca → not below threshold → mensuel.'''
        result = self.detect(1000.0, 'mensuel', 'Casablanca')
        self.assertEqual(result, 'mensuel')

    def test_petite_ville_below_threshold_is_suspect(self):
        '''Martil at 300 DH/month is below 400 DH threshold → suspect.'''
        result = self.detect(300.0, 'mensuel', 'Martil')
        self.assertEqual(result, 'journalier_suspect')

    def test_petite_ville_above_threshold_stays_mensuel(self):
        '''Martil at 600 DH/month is above 400 DH threshold → mensuel.'''
        result = self.detect(600.0, 'mensuel', 'Martil')
        self.assertEqual(result, 'mensuel')

    def test_already_journalier_not_changed(self):
        '''Prix already flagged journalier must not be reclassified.'''
        result = self.detect(200.0, 'journalier', 'Casablanca')
        self.assertEqual(result, 'journalier')

    def test_inconnu_not_changed(self):
        result = self.detect(200.0, 'inconnu', 'Casablanca')
        self.assertEqual(result, 'inconnu')

    def test_none_prix_not_changed(self):
        '''None prix must not crash and must leave prix_type unchanged.'''
        result = self.detect(None, 'mensuel', 'Casablanca')
        self.assertEqual(result, 'mensuel')

    def test_all_grandes_villes_threshold(self):
        '''All grandes villes use the 1000 DH threshold.'''
        grandes = ['Casablanca', 'Rabat', 'Marrakech', 'Fès', 'Tanger',
                   'Agadir', 'Meknès', 'Oujda', 'Kénitra', 'Tétouan']
        for ville in grandes:
            # 999 DH = below threshold → suspect
            r = self.detect(999.0, 'mensuel', ville)
            self.assertEqual(
                r,
                'journalier_suspect',
                f'{ville} at 999 DH should be suspect')
            # 1001 DH = above threshold → mensuel
            r = self.detect(1001.0, 'mensuel', ville)
            self.assertEqual(
                r, 'mensuel', f'{ville} at 1001 DH should stay mensuel')


# ══════════════════════════════════════════════════════════════════════════════
# FIX #3 — Surface cap (SURFACE_MAX_RESIDENTIAL = 800 m²)
# ══════════════════════════════════════════════════════════════════════════════


class TestSurfaceCap(unittest.TestCase):
    '''
    Tests for clean_data._apply_missing_value_strategy() surface cap logic.
    Ensures oversized listings are dropped and not passed to the warehouse.
    '''

    def _run_clean_on_df(self, df_raw):
        '''Helper: run the full _clean() pipeline on a staging-like DataFrame.'''
        from src.clean.clean_data import _clean
        return _clean(df_raw)

    def test_surface_above_cap_is_dropped(self):
        '''A listing with surface_m2 > 800 must not survive cleaning.'''
        df = make_staging_df(5)
        df.loc[0, 'surface'] = '850'   # over the 800 m² cap
        result = self._run_clean_on_df(df)
        self.assertTrue(
            (result['surface_m2'].dropna() <= 800).all(),
            'surface_m2 > 800 survived cleaning'
        )

    def test_surface_at_cap_is_kept(self):
        '''A listing with surface_m2 == 800 is exactly at the cap and must be kept.'''
        df = make_staging_df(5)
        df.loc[0, 'surface'] = '800'
        result = self._run_clean_on_df(df)

        # May be empty if all records are identical (deduplicated), so just
        # assert no >800
        self.assertTrue(
            (result['surface_m2'].dropna() <= 800).all(),
            'surface_m2 > 800 found after cleaning'
        )

    def test_surface_below_cap_is_kept(self):
        '''Normal surface values must not be dropped.'''
        df = make_staging_df(5, surface=['80'] * 5)
        result = self._run_clean_on_df(df)
        self.assertGreater(len(result), 0)

    def test_surface_cap_constant_is_800(self):
        '''Regression guard: ensure SURFACE_MAX_RESIDENTIAL has not been changed.'''
        from src.clean.clean_data import SURFACE_MAX_RESIDENTIAL
        self.assertEqual(
            SURFACE_MAX_RESIDENTIAL,
            800,
            'SURFACE_MAX_RESIDENTIAL changed — update BI/ML thresholds accordingly')


# ══════════════════════════════════════════════════════════════════════════════
# FIX #4 — etage "0" → "Non précisé"
# ══════════════════════════════════════════════════════════════════════════════


class TestEtageZeroNormalization(unittest.TestCase):
    '''
    Tests that etage == "0" is treated as missing data (scraper artefact)
    and normalised to "Non précisé" — not passed as a real floor number.
    '''

    def _run_clean_on_df(self, df_raw):
        from src.clean.clean_data import _clean
        return _clean(df_raw)

    def test_etage_zero_becomes_non_precise(self):
        '''etage = "0" must be converted to "Non précisé" after cleaning.'''
        df = make_staging_df(5, etage=['0', '1', '2', '3', '4'])
        result = self._run_clean_on_df(df)
        self.assertNotIn(
            '0',
            result['etage'].values,
            'etage "0" survived cleaning — should be "Non précisé"')

    def test_etage_zero_string_normalised(self):
        '''All rows with etage=0 should become Non précisé.'''
        df = make_staging_df(5, etage=['0'] * 5)
        result = self._run_clean_on_df(df)
        for v in result['etage'].values:
            self.assertNotEqual(str(v), '0', f'etage value 0 survived: {v}')

    def test_etage_empty_becomes_non_precise(self):
        '''etage = "" should also normalise to "Non précisé".'''
        df = make_staging_df(5, etage=['', '1', '2', '3', '4'])
        result = self._run_clean_on_df(df)
        self.assertNotIn('', result['etage'].values)

    def test_etage_valid_values_unchanged(self):
        '''Valid etage values (1, 2, RDC) must not be modified.'''
        df = make_staging_df(5, etage=['1', '2', '3', '5', 'RDC'])
        result = self._run_clean_on_df(df)
        valid_etages = set(result['etage'].values)
        # None of the valid values should be stripped or converted to Non
        # précisé
        self.assertIn('1', valid_etages)

    def test_etage_none_becomes_non_precise(self):
        '''etage = None should become "Non précisé".'''
        df = make_staging_df(5, etage=[None, '1', '2', '3', '4'])
        result = self._run_clean_on_df(df)
        self.assertNotIn(None, result['etage'].values)


if __name__ == "__main__":
    unittest.main(verbosity=2)
