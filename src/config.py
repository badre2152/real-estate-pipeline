"""
src/config.py
=============
Single source of truth for all pipeline configuration.

Every value here can be overridden by an environment variable of the same name,
so Docker / CI / production can tune behaviour without touching the code.

Usage:
    from src.config import MAX_PAGES, MIN_PRIX, AVITO_BASE_URL
"""

import os

# ── Scraper ─────────────────────────────────────────────────────────────
AVITO_BASE_URL = os.getenv("AVITO_BASE_URL", "https://www.avito.ma")
AVITO_RENT_URL = os.getenv(
    "AVITO_RENT_URL",
    "https://www.avito.ma/fr/maroc/immobilier-%C3%A0_louer",
)
# pages per run (~20 listings/page)
MAX_PAGES = int(os.getenv("MAX_PAGES", "25"))
# min seconds between detail requests
DELAY_MIN = float(os.getenv("DELAY_MIN", "2.5"))
# max seconds between detail requests
DELAY_MAX = float(os.getenv("DELAY_MAX", "5.0"))
SCRAPER_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "5")
                      )  # seconds to sleep on empty page

# ── Data quality thresholds ─────────────────────────────────────────────
# Bronze / staging
HARD_MIN_RECORDS = int(float(os.getenv("HARD_MIN_RECORDS", "5")))
HARD_MIN_PRIX_FILL_PCT = float(os.getenv("HARD_MIN_PRIX_FILL_PCT", "0.50"))
HARD_MIN_VILLE_FILL_PCT = float(os.getenv("HARD_MIN_VILLE_FILL_PCT", "0.50"))

# Silver / clean
HARD_MAX_DROP_RATE = float(os.getenv("HARD_MAX_DROP_RATE", "0.80"))
HARD_MIN_PRIX_FILL_STAGING = float(
    os.getenv("HARD_MIN_PRIX_FILL_STAGING", "0.50"))
SOFT_PRIX_M2_FILL = float(os.getenv("SOFT_PRIX_M2_FILL", "0.50"))

# ── Price & surface ranges ──────────────────────────────────────────────
# DH — below this = artefact
MIN_PRIX = float(os.getenv("MIN_PRIX", "100"))
MAX_PRIX = float(os.getenv("MAX_PRIX", "500000"))   # DH — above this = outlier
# m² — below this = artefact
MIN_SURFACE = float(os.getenv("MIN_SURFACE", "5"))
MAX_SURFACE = float(os.getenv("MAX_SURFACE", "5000"))     # m²

# ── Price categories (used in clean_data.py) ────────────────────────────
# Thresholds in DH/month — values below each threshold get that label
PRICE_CATEGORIES: list[tuple[float, str]] = [
    (float(os.getenv("PRIX_TRES_BAS_MAX", "3000")), "Très Bas"),
    (float(os.getenv("PRIX_BAS_MAX", "10000")), "Bas"),
    (float(os.getenv("PRIX_MOYEN_MAX", "25000")), "Moyen"),
    (float(os.getenv("PRIX_ELEVE_MAX", "999999")), "Élevé"),
]
VALID_CATEGORIES = [cat for _, cat in PRICE_CATEGORIES]

# ── GX (Great Expectations) ─────────────────────────────────────────────
GX_MIN_ROWS = int(os.getenv("GX_MIN_ROWS", "5"))
GX_MAX_ROWS = int(os.getenv("GX_MAX_ROWS", "10000"))
GX_PRIX_MOSTLY = float(os.getenv("GX_PRIX_MOSTLY", "0.50"))
GX_VILLE_MOSTLY = float(os.getenv("GX_VILLE_MOSTLY", "0.50"))
GX_SILVER_MOSTLY = float(os.getenv("GX_SILVER_MOSTLY", "0.99"))
GX_SURFACE_MOSTLY = float(os.getenv("GX_SURFACE_MOSTLY", "0.90"))

# ── Pipeline retry ──────────────────────────────────────────────────────
PIPELINE_MAX_RETRIES = int(os.getenv("PIPELINE_MAX_RETRIES", "3"))
PIPELINE_RETRY_DELAY = int(
    os.getenv(
        "PIPELINE_RETRY_DELAY",
        "10"))  # base seconds (exponential backoff)

# ── Database connection pool ────────────────────────────────────────────
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# ── Logging ─────────────────────────────────────────────────────────────
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

# ── Grandes villes (major cities — used for is_grande_ville flag) ───────
GRANDES_VILLES: set[str] = {
    v.strip() for v in os.getenv(
        "GRANDES_VILLES",
        "Casablanca,Rabat,Marrakech,Fès,Tanger,Agadir,Meknès,Oujda,Kénitra,Tétouan",
    ).split(",")}
