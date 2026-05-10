"""
Bronze → Staging Validation
============================
Validates raw scraped records BEFORE they are loaded into staging.raw_annonces.

Design principles:
- Hard rules  → raise BronzeValidationError  → pipeline ABORTS
- Soft rules  → log warnings                 → pipeline CONTINUES
- Every rule is explicit, named, and logged independently
- No transformations happen here — pure read-only checks

Usage (called automatically inside run_staging):
    from src.staging.bronze_validator import validate_bronze
    validate_bronze(records)          # raises on hard failure
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.utils.logger import get_logger
from src.config import (
    HARD_MIN_RECORDS,
    HARD_MIN_PRIX_FILL_PCT,
    HARD_MIN_VILLE_FILL_PCT,
    AVITO_BASE_URL,
)

logger = get_logger("bronze_validator")


# ── Custom exception ────────────────────────────────────────────────────

class BronzeValidationError(Exception):
    """Raised when a hard validation rule fails. Stops the pipeline."""


# ── Thresholds ──────────────────────────────────────────────────────────

SOFT_MIN_FILL_PCT = 0.40       # warn if field fill-rate below 40%

VALID_PRIX_TYPES = {"mensuel", "journalier", "journalier_suspect", "inconnu"}

# Cities that are known scraper artefacts — never valid ville values
ARTEFACT_VILLES = {
    "COURS ET FORMATIONS",
    "Cours Et Formations",
    "cours et formations",
    "STAGES",
    "Stages",
}

# Prix must be a number string with optional narrow-space separator and 'DH'
# Examples: "4 500 DH", "4\u202f500 DH", "12500DH", "12500"
_PRIX_PATTERN = re.compile(r"^[\d\u202f\s]+(?:\s*DH)?$", re.IGNORECASE)

# scraped_at must be parseable as ISO 8601
_SCRAPED_AT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)


# ── Individual rule functions ───────────────────────────────────────────

def _rule_min_records(records: list[dict]) -> None:
    """HARD: pipeline is useless with fewer than HARD_MIN_RECORDS records."""
    n = len(records)
    if n < HARD_MIN_RECORDS:
        raise BronzeValidationError(
            f"Only {n} records in bronze file — minimum required is "
            f"{HARD_MIN_RECORDS}. Possible bot-block or empty scrape."
        )
    logger.info(f"  ✅ record count: {n} (≥ {HARD_MIN_RECORDS})")


def _rule_required_keys(records: list[dict]) -> None:
    """HARD: every record must contain the required top-level keys."""
    REQUIRED_KEYS = {"titre", "prix", "ville", "lien", "scraped_at"}
    bad_indices = []
    for i, r in enumerate(records):
        missing = REQUIRED_KEYS - set(r.keys())
        if missing:
            bad_indices.append((i, missing))

    if bad_indices:
        detail = "; ".join(f"row {i}: missing {m}" for i, m in bad_indices[:5])
        raise BronzeValidationError(
            f"{len(bad_indices)} record(s) missing required keys. "
            f"First offenders: {detail}"
        )
    logger.info(f"  ✅ required keys present in all {len(records)} records")


def _rule_no_error_field(records: list[dict]) -> None:
    """SOFT: records with error != None were failed scrapes — warn about them."""
    error_records = [r for r in records if r.get("error") is not None]
    if error_records:
        logger.warning(
            f"  ⚠️  {len(error_records)} record(s) have error field set "
            "(will be skipped at insert). Errors: "
            + ", ".join(str(r['error']) for r in error_records[:3])
        )
    else:
        logger.info("  ✅ no error records")


def _rule_prix_fill_rate(records: list[dict]) -> None:
    """HARD: if most records have no prix, data is unusable."""
    valid = [r for r in records if r.get("prix") and str(r["prix"]).strip()]
    pct = len(valid) / len(records)
    if pct < HARD_MIN_PRIX_FILL_PCT:
        raise BronzeValidationError(
            f"prix fill rate is {pct:.0%} — below hard minimum of "
            f"{HARD_MIN_PRIX_FILL_PCT:.0%}. "
            "Scraper may have failed to extract prices."
        )
    logger.info(f"  ✅ prix fill rate: {pct:.0%} ({len(valid)}/{len(records)})")


def _rule_ville_fill_rate(records: list[dict]) -> None:
    """HARD: if most records have no ville, location data is unusable."""
    valid = [
        r for r in records if r.get("ville") and str(
            r["ville"]).strip() and str(
            r["ville"]).strip().upper() not in {
                v.upper() for v in ARTEFACT_VILLES}]
    pct = len(valid) / len(records)
    if pct < HARD_MIN_VILLE_FILL_PCT:
        raise BronzeValidationError(
            f"ville fill rate (excluding artefacts) is {pct:.0%} — "
            f"below hard minimum of {HARD_MIN_VILLE_FILL_PCT:.0%}."
        )
    logger.info(
        f"  ✅ ville fill rate: {pct:.0%} ({len(valid)}/{len(records)})")


def _rule_prix_format(records: list[dict]) -> None:
    """SOFT: prix values should match expected DH format."""
    malformed = []
    for i, r in enumerate(records):
        p = r.get("prix", "")
        if p and not _PRIX_PATTERN.match(str(p).strip()):
            malformed.append((i, p))

    if malformed:
        examples = malformed[:3]
        logger.warning(
            f"  ⚠️  {len(malformed)} record(s) have unexpected prix format. "
            f"Examples: {examples}"
        )
    else:
        logger.info("  ✅ prix format valid on all non-empty records")


def _rule_prix_type_values(records: list[dict]) -> None:
    """SOFT: prix_type should be one of the known enum values."""
    invalid = [
        (i, r.get("prix_type"))
        for i, r in enumerate(records)
        if r.get("prix_type") and r["prix_type"] not in VALID_PRIX_TYPES
    ]
    if invalid:
        logger.warning(
            f"  ⚠️  {len(invalid)} record(s) have unknown prix_type. "
            f"Examples: {invalid[:3]}"
        )
    else:
        logger.info("  ✅ prix_type values all valid")


def _rule_lien_format(records: list[dict]) -> None:
    """SOFT: every lien should be a valid avito.ma URL."""
    bad = []
    for i, r in enumerate(records):
        lien = r.get("lien", "")
        if not lien:
            bad.append((i, "empty"))
            continue
        if not lien.startswith(AVITO_BASE_URL):
            bad.append((i, lien[:60]))

    if bad:
        logger.warning(
            f"  ⚠️  {len(bad)} record(s) have invalid/missing lien. "
            f"Examples: {bad[:3]}"
        )
    else:
        logger.info("  ✅ all liens are valid avito.ma URLs")


def _rule_lien_uniqueness(records: list[dict]) -> None:
    """SOFT: duplicate liens in the same bronze file indicate a scraper loop."""
    from collections import Counter
    liens = [r.get("lien") for r in records if r.get("lien")]
    counts = Counter(liens)
    duplicates = {label for label, count in counts.items() if count > 1}
    if duplicates:
        logger.warning(
            f"  ⚠️  {len(duplicates)} duplicate lien(s) found within bronze file. "
            f"Examples: {list(duplicates)[:2]}")
    else:
        logger.info("  ✅ all liens unique within this bronze file")


def _rule_scraped_at_format(records: list[dict]) -> None:
    """SOFT: scraped_at must be a parseable ISO timestamp."""
    bad = []
    now = datetime.now(timezone.utc)
    for i, r in enumerate(records):
        ts = r.get("scraped_at", "")
        if not ts:
            bad.append((i, "empty"))
            continue
        if not _SCRAPED_AT_PATTERN.match(str(ts)):
            bad.append((i, ts))
            continue
        # Must not be in the future
        try:
            parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if parsed.replace(tzinfo=None) > now.replace(tzinfo=None):
                bad.append((i, f"future timestamp: {ts}"))
        except ValueError:
            bad.append((i, f"unparseable: {ts}"))

    if bad:
        logger.warning(
            f"  ⚠️  {len(bad)} record(s) have invalid scraped_at. "
            f"Examples: {bad[:3]}"
        )
    else:
        logger.info("  ✅ scraped_at format valid on all records")


def _rule_artefact_villes(records: list[dict]) -> None:
    """SOFT: detect known scraper artefacts in ville field."""
    artefacts = [
        (i, r.get("ville"))
        for i, r in enumerate(records)
        if r.get("ville", "").strip() in ARTEFACT_VILLES
    ]
    if artefacts:
        logger.warning(
            f"  ⚠️  {len(artefacts)} record(s) have artefact ville values "
            "(e.g. 'COURS ET FORMATIONS'). They will be filtered at clean step. "
            f"Indices: {[i for i, _ in artefacts]}")
    else:
        logger.info("  ✅ no artefact ville values found")


def _rule_soft_field_fill_rates(records: list[dict]) -> None:
    """SOFT: warn if any secondary field has a very low fill rate."""
    SECONDARY_FIELDS = [
        "surface", "nb_chambres", "nb_salles_bain", "etage",
        "quartier", "annee_construction",
    ]
    n = len(records)
    lines = []
    for field in SECONDARY_FIELDS:
        filled = sum(
            1 for r in records if r.get(field) and str(
                r[field]).strip())
        pct = filled / n
        status = "✅" if pct >= SOFT_MIN_FILL_PCT else "⚠️"
        lines.append(f"    {status} {field:<22}: {filled}/{n} ({pct:.0%})")

    logger.info("  Secondary field fill rates:\n" + "\n".join(lines))


# ── Public entry point ──────────────────────────────────────────────────

def validate_bronze(records: list[dict], is_incremental: bool = False) -> dict:
    """
    Run all validation rules against raw bronze records.

    Returns a summary dict with counts for logging/monitoring.
    Raises BronzeValidationError on any hard rule failure.
    """
    logger.info("=" * 50)
    logger.info("  BRONZE → STAGING VALIDATION")
    logger.info("=" * 50)

    warnings_count = 0
    hard_failures = 0

    # ── HARD rules — any failure aborts the pipeline ────────────────────────
    # In incremental mode, skip min_records check (few new records is normal)
    min_records_rule = [] if is_incremental else [("min_records", _rule_min_records)]
    hard_rules = min_records_rule + [
        ("required_keys", _rule_required_keys),
        ("prix_fill_rate", _rule_prix_fill_rate),
        ("ville_fill_rate", _rule_ville_fill_rate),
    ]

    for rule_name, rule_fn in hard_rules:
        try:
            rule_fn(records)
        except BronzeValidationError:
            hard_failures += 1
            raise   # propagate immediately — stops pipeline
        except Exception as e:
            hard_failures += 1
            raise BronzeValidationError(
                f"Unexpected error in hard rule '{rule_name}': {e}"
            ) from e

    # ── SOFT rules — failures are logged as warnings only ───────────────────
    soft_rules = [
        ("no_error_field", _rule_no_error_field),
        ("prix_format", _rule_prix_format),
        ("prix_type_values", _rule_prix_type_values),
        ("lien_format", _rule_lien_format),
        ("lien_uniqueness", _rule_lien_uniqueness),
        ("scraped_at_format", _rule_scraped_at_format),
        ("artefact_villes", _rule_artefact_villes),
        ("soft_fill_rates", _rule_soft_field_fill_rates),
    ]

    for rule_name, rule_fn in soft_rules:
        try:
            rule_fn(records)
        except Exception as e:
            warnings_count += 1
            logger.warning(f"  ⚠️  soft rule '{rule_name}' raised: {e}")

    # ── Summary ─────────────────────────────────────────────────────────────
    n_valid = sum(1 for r in records if not r.get("error"))
    summary = {
        "total_records": len(records),
        "valid_records": n_valid,
        "error_records": len(records) - n_valid,
        "hard_failures": hard_failures,
        "soft_warnings": warnings_count,
    }

    logger.info(
        "\n  VALIDATION SUMMARY\n"
        "  ─────────────────────────────\n"
        f"  Total records  : {summary['total_records']}\n"
        f"  Valid records  : {summary['valid_records']}\n"
        f"  Error records  : {summary['error_records']}\n"
        f"  Hard failures  : {summary['hard_failures']}\n"
        f"  Soft warnings  : {summary['soft_warnings']}\n"
        f"  STATUS         : {'✅ PASSED' if hard_failures == 0 else '❌ FAILED'}\n"
        f"  {'=' * 48}")

    return summary
