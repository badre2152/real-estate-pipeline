"""Verify ML failures are visible to pipeline callers."""

from unittest.mock import Mock

import pandas as pd
import pytest

from src.warehouse import ml_schema


def test_ml_loader_rejects_missing_columns(monkeypatch):
    monkeypatch.setattr(ml_schema, "execute_query", Mock())
    with pytest.raises(ValueError, match="Missing columns"):
        ml_schema.run_ml_schema(pd.DataFrame({"prix": [100]}))


def test_ml_loader_rejects_empty_frame(monkeypatch):
    monkeypatch.setattr(ml_schema, "execute_query", Mock())
    frame = pd.DataFrame(columns=ml_schema._COLS)
    with pytest.raises(ValueError, match="empty ML dataset"):
        ml_schema.run_ml_schema(frame)


def test_ml_loader_rejects_all_missing_prices(monkeypatch):
    monkeypatch.setattr(ml_schema, "execute_query", Mock())
    frame = pd.DataFrame([{col: None for col in ml_schema._COLS}])
    with pytest.raises(ValueError, match="valid prices"):
        ml_schema.run_ml_schema(frame)


def test_ml_export_propagates_csv_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ml_schema, "GOLD_ML_DIR", str(tmp_path))
    def fail_csv(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_csv", fail_csv)
    with pytest.raises(OSError, match="disk full"):
        ml_schema._save_gold_ml(pd.DataFrame({"prix": [100]}))


def test_ml_export_rejects_empty_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(ml_schema, "GOLD_ML_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="empty ML dataset"):
        ml_schema._save_gold_ml(pd.DataFrame())
