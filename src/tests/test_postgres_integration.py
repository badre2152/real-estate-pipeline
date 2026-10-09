"""Integration coverage for warehouse tables and PostgreSQL conflicts."""

import os

import psycopg2
import pytest

from src.warehouse.bi_schema import (
    _DDL as BI_DDL,
    _upsert_caracteristiques,
    _upsert_localisation,
)
from src.warehouse.ml_schema import _DDL_STATEMENTS as ML_DDL


@pytest.fixture
def database():
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("PostgreSQL integration environment is not configured")
    connection = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    try:
        with connection:
            with connection.cursor() as cursor:
                for statement in BI_DDL + ML_DDL:
                    cursor.execute(statement)
        yield connection
    finally:
        connection.close()


def test_warehouse_schema_and_dimension_upserts(database):
    with database:
        with database.cursor() as cursor:
            location_id = _upsert_localisation(
                cursor, "Integration City", "Integration Area", "Test", False
            )
            repeated_location = _upsert_localisation(
                cursor, "Integration City", "Integration Area", "Test", False
            )
            characteristics_id = _upsert_caracteristiques(cursor, 2, 1, "3")
            repeated_characteristics = _upsert_caracteristiques(
                cursor, 2, 1, "3"
            )
            assert location_id == repeated_location
            assert characteristics_id == repeated_characteristics
            cursor.execute(
                "SELECT COUNT(*) FROM bi_schema.dim_localisation "
                "WHERE ville = %s AND quartier = %s",
                ("Integration City", "Integration Area"),
            )
            assert cursor.fetchone()[0] == 1
            cursor.execute(
                "SELECT to_regclass('ml_schema.feature_store')"
            )
            assert cursor.fetchone()[0] is not None


def test_schema_migrations_are_idempotent(database):
    from src.utils.migrations import MIGRATIONS, run_all_migrations

    run_all_migrations()
    run_all_migrations()

    with database:
        with database.cursor() as cursor:
            cursor.execute("SELECT name FROM public.schema_migrations")
            names = {row[0] for row in cursor.fetchall()}

    assert names == {name for name, _ in MIGRATIONS}


def test_failed_migration_rolls_back_schema_and_tracking(database, monkeypatch):
    from src.utils import migrations

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        [
            (
                "integration_rollback_create",
                "CREATE TABLE public.integration_rollback_probe (id INTEGER)",
            ),
            ("integration_rollback_fail", "INVALID SQL STATEMENT"),
        ],
    )

    with pytest.raises(psycopg2.Error):
        migrations.run_all_migrations()

    with database:
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('public.integration_rollback_probe')"
            )
            assert cursor.fetchone()[0] is None
            cursor.execute(
                "SELECT COUNT(*) FROM public.schema_migrations "
                "WHERE name LIKE 'integration_rollback_%'"
            )
            assert cursor.fetchone()[0] == 0


def test_bi_load_rolls_back_all_rows_on_invalid_listing(database):
    import pandas as pd

    from src.warehouse.bi_schema import run_bi_schema

    link = "https://example.com/integration-bi-rollback"
    frame = pd.DataFrame([
        {
            "ville": "Rollback City",
            "quartier": "Area",
            "region_label": "Test",
            "is_grande_ville": False,
            "nb_chambres": 2,
            "nb_salles_bain": 1,
            "etage": "2",
            "scraped_at": "2026-10-01T10:00:00",
            "titre": "Test apartment",
            "prix": 1000,
            "prix_type": "mensuel",
            "surface_m2": 50,
            "prix_par_m2": 20,
            "categorie_prix": "Test",
            "lien": link,
        },
        {
            "ville": "Rollback City",
            "quartier": "Area",
            "region_label": "Test",
            "is_grande_ville": False,
            "nb_chambres": 2,
            "nb_salles_bain": 1,
            "etage": "2",
            "scraped_at": "not a timestamp",
            "titre": "Invalid apartment",
            "prix": 1000,
            "prix_type": "mensuel",
            "surface_m2": 50,
            "prix_par_m2": 20,
            "categorie_prix": "Test",
            "lien": "https://example.com/integration-bi-invalid",
        },
    ])

    with pytest.raises(RuntimeError, match="BI load rejected 1 of 2 rows"):
        run_bi_schema(frame)

    with database:
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM bi_schema.fact_annonce WHERE lien = %s",
                (link,),
            )
            assert cursor.fetchone()[0] == 0


def test_ml_upsert_refreshes_existing_listing(database):
    from psycopg2.extras import execute_values
    from src.warehouse.ml_schema import _INSERT

    link = "https://example.com/integration-ml-upsert"
    first = (
        1000, "Test City", "Area", 50, 2, 1, "2",
        20, "Standard", "mensuel", "Original", link, "2026-10-01",
    )
    updated = (
        1200, "Test City", "Area", 60, 2, 1, "2",
        20, "Standard", "mensuel", "Updated", link, "2026-10-02",
    )
    with database:
        with database.cursor() as cursor:
            execute_values(cursor, _INSERT, [first])
            execute_values(cursor, _INSERT, [updated])
            cursor.execute(
                "SELECT COUNT(*), MAX(prix), MAX(surface_m2), MAX(titre) "
                "FROM ml_schema.feature_store WHERE lien = %s",
                (link,),
            )
            assert cursor.fetchone() == (1, 1200, 60, "Updated")


def test_bi_fact_upsert_refreshes_existing_listing(database, monkeypatch):
    import pandas as pd
    from src.warehouse import bi_schema

    monkeypatch.setattr(bi_schema, "_save_gold_bi", lambda: None)
    monkeypatch.setattr(bi_schema, "_validate", lambda inserted_this_run: None)
    link = "https://example.com/integration-bi-upsert"
    listing = {
        "ville": "Upsert City",
        "quartier": "Area",
        "region_label": "Test",
        "is_grande_ville": False,
        "nb_chambres": 2,
        "nb_salles_bain": 1,
        "etage": "2",
        "scraped_at": "2026-10-01T10:00:00",
        "titre": "Original",
        "prix": 1000,
        "prix_type": "mensuel",
        "surface_m2": 50,
        "prix_par_m2": 20,
        "categorie_prix": "Test",
        "lien": link,
    }
    bi_schema.run_bi_schema(pd.DataFrame([listing]))
    listing.update(titre="Updated", prix=1200, surface_m2=60)
    bi_schema.run_bi_schema(pd.DataFrame([listing]))

    with database:
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*), MAX(prix), MAX(surface_m2), MAX(titre) "
                "FROM bi_schema.fact_annonce WHERE lien = %s",
                (link,),
            )
            assert cursor.fetchone() == (1, 1200, 60, "Updated")
