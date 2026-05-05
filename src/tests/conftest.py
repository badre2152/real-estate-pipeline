"""
tests/conftest.py
==================
Shared fixtures and factory helpers used across all test modules.

Compatible with pytest (recommended) and unittest.
Install pytest: pip install pytest pytest-cov
Run all tests : pytest tests/ -v
Run with cov  : pytest tests/ -v --cov=src --cov-report=term-missing
"""

import sys
import os

# ── Make src importable without installing the package ────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd
import numpy as np


# ══════════════════════════════════════════════════════════════════════════════
# BRONZE RECORD FACTORIES
# ══════════════════════════════════════════════════════════════════════════════

def make_bronze_record(**overrides) -> dict:
    """Return a minimal valid bronze record with optional field overrides."""
    base = {
        "titre"             : "Appartement 2 chambres à louer",
        "prix"              : "4 500 DH",
        "prix_type"         : "mensuel",
        "ville"             : "Casablanca",
        "quartier"          : "Maarif",
        "surface"           : "80",
        "nb_chambres"       : "2",
        "nb_salles_bain"    : "1",
        "etage"             : "3",
        "annee_construction": "2010",
        "lien"              : "https://www.avito.ma/fr/casablanca/appartements/appt-2ch-123456",
        "scraped_at"        : "2026-05-02T10:47:41",
        "error"             : None,
    }
    base.update(overrides)
    return base


def make_bronze_records(n: int = 10, **overrides) -> list[dict]:
    """Return n valid bronze records with unique liens."""
    return [
        make_bronze_record(
            lien=f"https://www.avito.ma/fr/casablanca/appartements/appt-{i}",
            **overrides,
        )
        for i in range(n)
    ]


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN DATAFRAME FACTORIES
# ══════════════════════════════════════════════════════════════════════════════

def make_clean_df(n: int = 10, **col_overrides) -> pd.DataFrame:
    """
    Return a valid clean DataFrame with n rows.
    All required post-clean columns are present and valid.
    """
    base = {
        "prix"              : [5000.0] * n,
        "prix_type"         : ["mensuel"] * n,
        "ville"             : ["Casablanca"] * n,
        "quartier"          : ["Maarif"] * n,
        "surface_m2"        : [80.0] * n,
        "nb_chambres"       : [2.0] * n,
        "nb_salles_bain"    : [1.0] * n,
        "etage"             : ["3"] * n,
        "lien"              : [f"https://www.avito.ma/fr/casablanca/appt-{i}" for i in range(n)],
        "scraped_at"        : ["2026-05-02T10:47:41"] * n,
        "prix_par_m2"       : [62.5] * n,
        "categorie_prix"    : ["Moyen"] * n,
        "region_label"      : ["Casablanca-Settat"] * n,
        "is_grande_ville"   : [True] * n,
        "age_bien"          : [16] * n,
        "annee_construction": [2010] * n,
    }
    base.update(col_overrides)
    return pd.DataFrame(base)


def make_staging_df(n: int = 10, **col_overrides) -> pd.DataFrame:
    """Return a valid staging DataFrame (all TEXT columns, like staging.raw_annonces)."""
    base = {
        "prix"              : ["4 500 DH"] * n,
        "prix_type"         : ["mensuel"] * n,
        "ville"             : ["Casablanca"] * n,
        "quartier"          : ["Maarif"] * n,
        "surface"           : ["80"] * n,
        "nb_chambres"       : ["2"] * n,
        "nb_salles_bain"    : ["1"] * n,
        "etage"             : ["3"] * n,
        "lien"              : [f"https://www.avito.ma/fr/casablanca/appt-{i}" for i in range(n)],
        "scraped_at"        : ["2026-05-02T10:47:41"] * n,
    }
    base.update(col_overrides)
    return pd.DataFrame(base)
