"""Verify warehouse queries use the pooled PostgreSQL connection."""

from unittest.mock import Mock

import pandas as pd
import pytest

from src.warehouse import bi_schema, ml_schema


@pytest.mark.parametrize("module", [bi_schema, ml_schema])
def test_fetch_clean_releases_connection(module, monkeypatch):
    connection = Mock()
    expected = pd.DataFrame({"prix": [100]})
    read_sql = Mock(return_value=expected)
    release = Mock()
    monkeypatch.setattr(module, "get_connection", lambda: connection)
    monkeypatch.setattr(module, "release_connection", release)
    monkeypatch.setattr(module.pd, "read_sql", read_sql)

    if module is bi_schema:
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [(100,)]
        cursor.description = [("prix",)]
        result = module._fetch_clean()
        cursor.close.assert_called_once()
    else:
        result = module._fetch_clean()
        read_sql.assert_called_once_with(
            "SELECT * FROM clean.annonces", connection
        )

    assert result.equals(expected)
    release.assert_called_once_with(connection)
