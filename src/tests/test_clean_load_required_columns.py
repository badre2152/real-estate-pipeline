"""Verify that missing required clean columns fail the database load."""

from unittest.mock import patch

import pandas as pd
import pytest

from src.clean.clean_data import _load_to_db


def test_missing_required_columns_fail_before_bulk_insert():
    with patch("src.clean.clean_data.execute_query") as execute_query:
        with patch("src.clean.clean_data.bulk_insert") as bulk_insert:
            with pytest.raises(ValueError, match="Missing columns"):
                _load_to_db(pd.DataFrame({"prix": [5000]}))

    assert execute_query.call_count == 2
    bulk_insert.assert_not_called()
