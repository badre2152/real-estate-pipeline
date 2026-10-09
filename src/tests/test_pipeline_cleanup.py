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
