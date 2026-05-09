import logging
import os
from logging.handlers import RotatingFileHandler

# ── Paths ───────────────────────────────────────────────────────────────
_THIS_FILE = os.path.abspath(__file__)
LOG_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(_THIS_FILE),
        "../../logs"))

# ── Rotation settings (from config) ─────────────────────────────────────
# Import lazily to avoid circular imports (logger is imported by config
# indirectly)
try:
    from src.config import LOG_MAX_BYTES as _MAX_BYTES, LOG_BACKUP_COUNT as _BACKUP_COUNT
except ImportError:
    _MAX_BYTES = 5 * 1024 * 1024
    _BACKUP_COUNT = 5

# Loggers that get their own dedicated log file.
# Key   = logger name passed to get_logger()
# Value = log filename inside LOG_DIR
_STAGE_FILES = {
    "pipeline": "pipeline.log",
    "scraper": "scraper.log",
    "staging": "staging.log",
    "bronze_validator": "staging.log",    # same file as staging (same phase)
    "clean": "clean.log",
    "clean_validator": "clean.log",      # same file as clean (same phase)
    "bi_schema": "bi_schema.log",
    "ml_schema": "ml_schema.log",
    "gx_bronze": "gx_bronze.log",
    "gx_silver": "gx_silver.log",
    "migrations": "migrations.log",
    "db": "pipeline.log",   # db errors go to pipeline.log only
}

_FORMATTER = logging.Formatter(
    "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _make_rotating_handler(filepath: str) -> RotatingFileHandler:
    """Return a RotatingFileHandler that appends to *filepath*."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    fh = RotatingFileHandler(
        filepath,
        mode="a",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_FORMATTER)
    return fh


def get_logger(name: str) -> logging.Logger:
    """
    Return a configured logger for *name*.

    Every logger:
      - prints INFO+ to the console
      - writes DEBUG+ to  logs/pipeline.log   (shared, always)
      - writes DEBUG+ to  logs/<stage>.log     (per-stage, when name is known)

    All file handlers use RotatingFileHandler (5 MB / 5 backups).
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger                          # already configured

    logger.setLevel(logging.DEBUG)
    # prevent duplicate messages via root logger
    logger.propagate = False

    # ── Console handler ─────────────────────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(_FORMATTER)
    logger.addHandler(ch)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        # ── Shared pipeline.log (every logger writes here) ───────────────────
        pipeline_log = os.path.join(LOG_DIR, "pipeline.log")
        logger.addHandler(_make_rotating_handler(pipeline_log))

        # ── Per-stage log file (only for known stage names) ──────────────────
        stage_filename = _STAGE_FILES.get(name)
        if stage_filename and stage_filename != "pipeline.log":
            stage_log = os.path.join(LOG_DIR, stage_filename)
            logger.addHandler(_make_rotating_handler(stage_log))

    except Exception as exc:
        logger.warning(
            "File logging disabled — could not create log files in "
            f"{LOG_DIR!r}: {exc}"
        )

    return logger
