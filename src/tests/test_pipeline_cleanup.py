"""Regression coverage for database pool cleanup when a pipeline run aborts."""

import pytest

from src import pipeline


def test_pipeline_closes_pool_when_a_stage_fails(monkeypatch):
    closed = []

    def fail_step(*args, **kwargs):
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(pipeline, "_run", fail_step)
    monkeypatch.setattr(pipeline, "close_pool", lambda: closed.append(True))

    with pytest.raises(SystemExit) as exc:
        pipeline.run_pipeline()

    assert exc.value.code == 1
    assert closed == [True]


def test_pipeline_aborts_on_empty_extraction(monkeypatch):
    closed = []
    monkeypatch.setattr(pipeline, "close_pool", lambda: closed.append(True))
    monkeypatch.setattr(pipeline, "_run", lambda name, fn, *args, **kwargs: [] if name == "EXTRACT" else None)

    with pytest.raises(SystemExit) as exc:
        pipeline.run_pipeline()

    assert exc.value.code == 1
    assert closed == [True]


def test_pipeline_aborts_on_empty_cleaned_data(monkeypatch):
    import pandas as pd

    closed = []
    monkeypatch.setattr(pipeline, "close_pool", lambda: closed.append(True))

    def run_stage(name, fn, *args, **kwargs):
        if name == "EXTRACT":
            return [{"lien": "https://example.com/listing"}]
        if name == "STAGING":
            return "test_run"
        if name == "CLEAN":
            return pd.DataFrame()
        return None

    monkeypatch.setattr(pipeline, "_run", run_stage)
    monkeypatch.setattr(pipeline, "GX_ENABLED", False)

    with pytest.raises(SystemExit) as exc:
        pipeline.run_pipeline()

    assert exc.value.code == 1
    assert closed == [True]
