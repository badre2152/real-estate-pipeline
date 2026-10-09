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
