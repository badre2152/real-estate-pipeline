"""Regression coverage for persistent incremental listing history."""

from unittest.mock import patch

from src.extract.scraper import _get_known_liens


def test_incremental_history_uses_clean_storage():
    with patch("src.extract.scraper.fetch_all") as fetch:
        fetch.return_value = [("https://www.avito.ma/listing-1",)]
        links = _get_known_liens()

    assert links == {"https://www.avito.ma/listing-1"}
    fetch.assert_called_once_with(
        "SELECT lien FROM clean.annonces WHERE lien IS NOT NULL;"
    )
