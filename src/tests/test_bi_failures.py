"""Verify that BI load failures are reported to the caller."""

from unittest.mock import Mock

import pytest

from src.warehouse import bi_schema


def test_validation_fails_on_orphan_dimensions(monkeypatch):
    results = iter([[(3,)], [(1,)], [(0,)]])
    monkeypatch.setattr(bi_schema, "fetch_all", lambda query: next(results))

    with pytest.raises(RuntimeError, match="BI validation failed"):
        bi_schema._validate(inserted_this_run=3)


def test_validation_passes_without_orphans(monkeypatch):
    results = iter([[(3,)], [(0,)], [(0,)]])
    monkeypatch.setattr(bi_schema, "fetch_all", lambda query: next(results))

    bi_schema._validate(inserted_this_run=3)


def test_export_propagates_query_error(monkeypatch, tmp_path):
    connection = Mock()
    monkeypatch.setattr(bi_schema, "GOLD_BI_DIR", str(tmp_path))
    monkeypatch.setattr(bi_schema, "get_connection", lambda: connection)
    release = Mock()
    monkeypatch.setattr(bi_schema, "release_connection", release)

    def fail_query(*args, **kwargs):
        raise ValueError("database query failed")

    monkeypatch.setattr(bi_schema.pd, "read_sql", fail_query)

    with pytest.raises(ValueError, match="database query failed"):
        bi_schema._save_gold_bi()

    release.assert_called_once_with(connection)


def test_export_rejects_empty_results(monkeypatch, tmp_path):
    import pandas as pd

    connection = Mock()
    monkeypatch.setattr(bi_schema, "GOLD_BI_DIR", str(tmp_path))
    monkeypatch.setattr(bi_schema, "get_connection", lambda: connection)
    monkeypatch.setattr(bi_schema, "release_connection", Mock())
    monkeypatch.setattr(bi_schema.pd, "read_sql", lambda *args, **kwargs: pd.DataFrame())

    with pytest.raises(RuntimeError, match="Gold BI export produced no data"):
        bi_schema._save_gold_bi()
