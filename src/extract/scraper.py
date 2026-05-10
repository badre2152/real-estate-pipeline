import time
import json
import os
import random
import re
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    InvalidSessionIdException,
    StaleElementReferenceException,
)

from src.utils.logger import get_logger
from src.utils.db import fetch_all
from src.config import (
    AVITO_RENT_URL as BASE_URL,
    MAX_PAGES,
    DELAY_MIN,
    DELAY_MAX,
    SCRAPER_TIMEOUT,
)

logger = get_logger("scraper")
BRONZE_DIR = os.path.join(os.path.dirname(__file__), "../../data/bronze")

# Incremental scraping — stop after this many consecutive known listings
_INCREMENTAL_STOP_AFTER = 5

# ✅ FIX: كلمات مفتاحية محدّثة — تضيف studio/louer، تحذف terrain/ferme
IMMOBILIER_KEYWORDS = [
    "appartement", "appartements",
    "maison", "maisons",
    "villa", "villas",
    "studio", "studios",
    "bureau", "bureaux",
    "riad", "local",
    "immobilier",
    "louer",
]

# ✅ FIX: قائمة موسّعة لمنع التقاط نصوص خاطئة كـ quartier
LOCATION_FALSE_POSITIVES = [
    "vendre", "louer", "categorie", "annonce",
    "immobilier", "appartement", "appartements",
    "maison", "maisons", "villa", "villas",
    "terrain", "terrains", "bureau", "riad",
    "local", "ferme", "résidentiel", "commercial",
    # ✅ إضافات جديدة لمنع "Femmes De Ménage" وما شابهها
    "femmes", "ménage", "service", "agent",
    "agence", "contact", "appel", "whatsapp",
    "référence", "ref", "code",
]

# ✅ FIX: مفردات تدل على سعر يومي — يجب الإشارة إليها في السجل
DAILY_RENTAL_KEYWORDS = [
    "jour", "journée", "quotidien", "quotidiennement",
    "vacances", "nuit", "nuitée", "week-end",
]


# ── Driver ──────────────────────────────────────────────────────────────

def _build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--js-flags=--max-old-space-size=512")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    chromium_bin = "/usr/bin/chromium"
    if os.path.exists(chromium_bin):
        options.binary_location = chromium_bin
        driver = webdriver.Chrome(
            service=Service("/usr/bin/chromedriver"), options=options
        )
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=options
        )

    driver.set_page_load_timeout(30)
    return driver


# ── Helpers ─────────────────────────────────────────────────────────────

def _safe_text(driver, css: str, default: str = "") -> str:
    try:
        return driver.find_element(By.CSS_SELECTOR, css).text.strip()
    except (NoSuchElementException, StaleElementReferenceException):
        return default


def _get_listing_urls(driver, page_url: str) -> list[str]:
    urls = []
    try:
        driver.get(page_url)
        WebDriverWait(
            driver, 15).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a[href$='.htm']")))
        seen = set()
        for a in driver.find_elements(By.CSS_SELECTOR, "a[href$='.htm']"):
            try:
                href = a.get_attribute("href")
            except StaleElementReferenceException:
                continue
            if href and href not in seen:
                # Both conditions must be true: valid property type AND rental
                # signal
                is_rental_type = any(kw in href for kw in IMMOBILIER_KEYWORDS)
                is_louer = "louer" in href or "%C3%A0_louer" in href or "location" in href
                if is_rental_type and is_louer:
                    seen.add(href)
                    urls.append(href)
        logger.info(
            f"Page {page_url} → {len(urls)} annonces à louer trouvées.")
    except TimeoutException:
        logger.warning(f"Timeout on results page: {page_url}")
    except Exception as e:
        logger.error(f"Error fetching results page {page_url}: {e}")
    return urls


def _scrape_listing(driver, url: str) -> dict:
    record = {
        "titre": "",
        "prix": "",
        "prix_type": "mensuel",   # ✅ nouveau champ: mensuel | journalier | inconnu
        "ville": "",
        "quartier": "",
        "surface": "",
        "nb_chambres": "",
        "nb_salles_bain": "",
        "etage": "",
        "annee_construction": "",
        "lien": url,
        "scraped_at": datetime.utcnow().isoformat(),
        "error": None,
    }
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
        )

        # ── Titre ──────────────────────────────────────────────────────────
        titre = _safe_text(driver, "h1")
        # ✅ FIX: حذف كود الوكالة من العنوان (مثال: RBA-YA-1053 - Appartement...)
        titre = re.sub(
            r'^[A-Z]{2,5}-[A-Z]{2,5}-\d+\s*[-–]\s*',
            '',
            titre).strip()
        record["titre"] = titre

        # ✅ FIX: كشف الإيجار اليومي وتسجيله
        titre_lower = titre.lower()
        if any(kw in titre_lower for kw in DAILY_RENTAL_KEYWORDS):
            record["prix_type"] = "journalier"
            logger.warning(f"⚠️ Possible daily rental: {titre[:60]}")

        # ── Prix ───────────────────────────────────────────────────────────
        # Strategy: look for a span/p that contains digits + "DH".
        # Accept if it also contains "mois" (monthly price) or not.
        # Prefer shorter text (avoids capturing full sentences).
        prix_candidates = []
        for el in driver.find_elements(By.CSS_SELECTOR, "span, p"):
            try:
                txt = el.text.strip()
            except StaleElementReferenceException:
                continue
            if re.search(r'[\d\s\u202f]+DH', txt) and len(txt) < 50:
                prix_candidates.append(txt)
        # Pick the shortest candidate (most likely to be the price itself)
        if prix_candidates:
            record["prix"] = min(prix_candidates, key=len)

        # ── Localisation ───────────────────────────────────────────────────
        # ✅ FIX: قائمة موسّعة + تحقق مشدّد من الجانبين
        for el in driver.find_elements(By.CSS_SELECTOR, "span, p, a"):
            try:
                txt = el.text.strip()
            except StaleElementReferenceException:
                continue
            if "," in txt and 3 < len(txt) < 50:
                txt_lower = txt.lower()
                if not any(w in txt_lower for w in LOCATION_FALSE_POSITIVES):
                    parts = [p.strip() for p in txt.split(",")]
                    if len(parts) == 2 and all(parts):
                        # ✅ FIX: التحقق أن كلا الجزأين أحرف فقط (ليس أرقام أو رموز)
                        if all(
                            re.match(
                                r"^[\w\s\-\.'éèêëàâùûüîïôç]+$",
                                p,
                                re.UNICODE) for p in parts):
                            record["quartier"] = parts[0]
                            record["ville"] = parts[1]
                            break

        # ── Attributs ──────────────────────────────────────────────────────
        for el in driver.find_elements(By.CSS_SELECTOR, "span, div, p"):
            try:
                txt = el.text.strip()
            except StaleElementReferenceException:
                continue

            if "\n" not in txt or len(txt) > 60:
                continue

            parts = [p.strip() for p in txt.split("\n") if p.strip()]
            if len(parts) != 2:
                continue

            value, label = parts[0], parts[1].lower()

            if "surface" in label:
                record["surface"] = value
            elif "chambre" in label or "pièce" in label:
                record["nb_chambres"] = value
            elif "salle" in label or "bain" in label:
                record["nb_salles_bain"] = value
            elif "étage" in label or "etage" in label:
                record["etage"] = value
            elif "année" in label or "construction" in label:
                record["annee_construction"] = value

        logger.debug(
            f"Scraped: {str(record.get('titre', ''))[:50]} | "
            f"prix={record.get('prix')} | ville={record.get('ville')} | "
            f"prix_type={record.get('prix_type')}"
        )

    except TimeoutException:
        logger.warning(f"Timeout on listing: {url}")
        record["error"] = "timeout"
    except InvalidSessionIdException:
        raise
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        record["error"] = str(e)

    return record


# ── Bronze persistence ──────────────────────────────────────────────────

def _save_bronze(records: list[dict]) -> str:
    from datetime import timezone
    now = datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    date_pfx = now.strftime("%Y/%m/%d")
    part_dir = os.path.join(BRONZE_DIR, date_pfx)
    os.makedirs(part_dir, exist_ok=True)
    path = os.path.join(part_dir, f"avito_raw_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    logger.info(f"Bronze saved → {path}  ({len(records)} records)")
    return path


# ── Fill rate monitor ───────────────────────────────────────────────────

def _log_fill_rates(records: list[dict]) -> None:
    if not records:
        return
    total = len(records)
    key_fields = [
        "prix", "ville", "quartier", "surface",
        "nb_chambres", "nb_salles_bain", "annee_construction",
    ]
    lines = [f"\n📊 PAGE STATS — {total} records"]
    for field in key_fields:
        filled = sum(1 for r in records if r.get(field) not in ("", None))
        pct = (filled / total) * 100
        status = "✅" if pct >= 80 else ("⚠️" if pct >= 40 else "❌")
        lines.append(f"  {status} {field:<22}: {pct:.1f}%  ({filled}/{total})")
    logger.info("\n".join(lines))

    # ✅ FIX: إحصائيات prix_type
    daily_count = sum(1 for r in records if r.get("prix_type") == "journalier")
    if daily_count:
        logger.warning(
            f"⚠️ {daily_count}/{total} إعلانات إيجار يومي — راجعها يدوياً")


# ── Validity guard ──────────────────────────────────────────────────────

def _is_valid_record(record: dict) -> bool:
    """
    ✅ FIX: استعادة حارس الصحة المحذوف.
    سجل صالح يجب أن يحتوي على سعر ومدينة على الأقل.
    """
    return bool(record.get("prix")) and bool(record.get("ville"))


# ── Incremental scraping ────────────────────────────────────────────────

def _get_known_liens() -> set[str]:
    """
    Fetch all liens already stored in staging.raw_annonces.
    Returns an empty set if DB is unreachable or table doesn't exist yet.
    """
    try:
        rows = fetch_all("SELECT lien FROM staging.raw_annonces WHERE lien IS NOT NULL;")
        known = {row[0] for row in rows}
        logger.info(f"Incremental mode: {len(known)} liens already in DB.")
        return known
    except Exception as e:
        logger.warning(f"Could not fetch known liens (fresh DB?): {e} — running full scrape.")
        return set()


def _is_already_scraped(url: str, known_liens: set[str]) -> bool:
    """Return True if this URL already exists in staging."""
    return url in known_liens


# ── Entry point ─────────────────────────────────────────────────────────

def run_scraper(max_pages: int = MAX_PAGES) -> list[dict]:
    logger.info("=== Scraper started ===")
    driver = _build_driver()
    all_records: list[dict] = []
    skipped = 0

    # ── Incremental: load known liens from DB once ─────────────────────
    known_liens = _get_known_liens()
    consecutive_known = 0
    incremental_stopped = False

    try:
        for page_num in range(1, max_pages + 1):
            page_url = f"{BASE_URL}?page={page_num}"
            logger.info(f"── Results page {page_num}/{max_pages}")

            listing_urls = []
            for attempt in range(3):
                listing_urls = _get_listing_urls(driver, page_url)
                if listing_urls:
                    break
                logger.warning(
                    f"Attempt {attempt + 1}: no URLs found, retrying…")
                time.sleep(SCRAPER_TIMEOUT)

            if not listing_urls:
                logger.warning("No listings found — stopping pagination.")
                break

            for url in listing_urls:

                # ── Incremental check ──────────────────────────────────
                if known_liens and _is_already_scraped(url, known_liens):
                    consecutive_known += 1
                    logger.debug(
                        f"[incremental] Known listing ({consecutive_known}/"
                        f"{_INCREMENTAL_STOP_AFTER}): {url}"
                    )
                    if consecutive_known >= _INCREMENTAL_STOP_AFTER:
                        logger.info(
                            f"[incremental] {_INCREMENTAL_STOP_AFTER} consecutive known "
                            f"listings — stopping early. "
                            f"{len(all_records)} new records collected."
                        )
                        incremental_stopped = True
                        break
                    continue   # skip scraping this URL
                else:
                    consecutive_known = 0  # reset counter on new listing

                try:
                    record = _scrape_listing(driver, url)

                except InvalidSessionIdException:
                    logger.warning("⚠️ Chrome crashed — restarting driver...")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = _build_driver()
                    logger.info("✅ Driver restarted — retrying URL...")
                    try:
                        record = _scrape_listing(driver, url)
                    except Exception as retry_exc:
                        logger.error(f"Retry failed for {url}: {retry_exc}")
                        record = {
                            "error": str(retry_exc), "lien": url,
                            "titre": "", "prix": "", "prix_type": "inconnu",
                            "ville": "", "quartier": "", "surface": "",
                            "nb_chambres": "", "nb_salles_bain": "",
                            "etage": "", "annee_construction": "",
                            "scraped_at": datetime.utcnow().isoformat(),
                        }

                except Exception as e:
                    logger.error(f"Unexpected error on {url}: {e}")
                    record = {
                        "error": str(e), "lien": url,
                        "titre": "", "prix": "", "prix_type": "inconnu",
                        "ville": "", "quartier": "", "surface": "",
                        "nb_chambres": "", "nb_salles_bain": "",
                        "etage": "", "annee_construction": "",
                        "scraped_at": datetime.utcnow().isoformat(),
                    }

                if record.get("error"):
                    skipped += 1
                elif _is_valid_record(record):
                    all_records.append(record)
                else:
                    skipped += 1
                    logger.warning(
                        "❌ Invalid record skipped (missing prix/ville): "
                        f"{record.get('titre', url)[:60]}"
                    )

                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

            if incremental_stopped:
                break

    except WebDriverException as e:
        logger.error(f"WebDriver fatal error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        logger.info("WebDriver closed.")

    if all_records:
        _save_bronze(all_records)
        _log_fill_rates(all_records)
    else:
        logger.info("No new records — bronze file not written.")

    mode = "incremental (stopped early)" if incremental_stopped else (
           "incremental (full scan)" if known_liens else "full (fresh DB)")
    logger.info(
        f"=== Scraper finished [{mode}] — {len(all_records)} new records "
        f"| {skipped} skipped ==="
    )
    return all_records
