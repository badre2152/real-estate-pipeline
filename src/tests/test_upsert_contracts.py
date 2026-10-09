"""Check that repeat-listing SQL refreshes every persisted attribute.

These checks verify SQL contracts without requiring a live database. A PostgreSQL
integration run is still needed to verify real conflict handling.
"""

import re

from src.staging.load_staging import _INSERT as STAGING_INSERT
from src.clean.clean_data import _INSERT as CLEAN_INSERT
from src.warehouse.ml_schema import _INSERT as ML_INSERT


def _assigned_columns(sql: str) -> set[str]:
    update_clause = sql.split("ON CONFLICT (lien) DO UPDATE SET", 1)[1]
    return set(re.findall(r"\b([a-z_][a-z0-9_]*)\s*=\s*EXCLUDED\.\1\b", update_clause))


def test_staging_upsert_refreshes_all_listing_attributes():
    expected = {
        "run_id", "titre", "prix", "prix_type", "ville", "quartier",
        "surface", "nb_chambres", "nb_salles_bain", "etage", "scraped_at",
    }
    assert expected <= _assigned_columns(STAGING_INSERT)


def test_clean_upsert_refreshes_all_listing_attributes():
    expected = {
        "titre", "prix", "prix_type", "ville", "quartier", "surface_m2",
        "nb_chambres", "nb_salles_bain", "etage", "scraped_at",
        "prix_par_m2", "categorie_prix", "region_label", "is_grande_ville",
    }
    assert expected <= _assigned_columns(CLEAN_INSERT)


def test_ml_upsert_refreshes_all_listing_attributes():
    expected = {
        "prix", "ville", "quartier", "surface_m2", "nb_chambres",
        "nb_salles_bain", "etage", "prix_par_m2", "categorie_prix",
        "prix_type", "titre", "scraped_at",
    }
    assert expected <= _assigned_columns(ML_INSERT)


def test_bi_fact_upsert_refreshes_all_listing_attributes():
    from pathlib import Path

    sql_source = (
        Path(__file__).resolve().parents[1] / "warehouse" / "bi_schema.py"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"INSERT INTO bi_schema\.fact_annonce\b(.*?)\"\"\"",
        sql_source,
        re.DOTALL,
    )
    assert match is not None
    expected = {
        "id_localisation", "id_caracteristiques", "id_temps", "titre",
        "prix", "prix_type", "surface_m2", "prix_par_m2", "categorie_prix",
    }
    assert expected <= _assigned_columns(match.group(1))
