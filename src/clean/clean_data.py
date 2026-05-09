"""
Clean layer — reads from staging.raw_annonces, applies full cleaning
+ feature engineering, writes to clean.annonces and data/silver/.
"""

import re
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.utils.db import get_connection, release_connection, execute_query, bulk_insert
from src.utils.logger import get_logger
from src.clean.clean_validator import validate_pre_clean, validate_post_clean, CleanValidationError
from src.config import GRANDES_VILLES as _GRANDES_VILLES

logger = get_logger("clean")
SILVER_DIR = os.path.join(os.path.dirname(__file__), "../../data/silver")

# ── DDL ─────────────────────────────────────────────────────────────────

_DDL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS clean;"

_DDL_TABLE = """
CREATE TABLE IF NOT EXISTS clean.annonces (
    id                  SERIAL PRIMARY KEY,
    titre               TEXT,
    prix                NUMERIC,
    prix_type           TEXT DEFAULT 'mensuel',
    ville               TEXT,
    quartier            TEXT,
    surface_m2          NUMERIC,
    nb_chambres         INTEGER,
    nb_salles_bain      INTEGER,
    etage               TEXT,
    lien                TEXT UNIQUE,
    scraped_at          TIMESTAMP,
    prix_par_m2         NUMERIC,
    categorie_prix      TEXT,
    region_label        TEXT,
    is_grande_ville     BOOLEAN,
    loaded_at           TIMESTAMP DEFAULT NOW()
);
"""

# FIX #14: Migrations moved to src/utils/migrations.py
_DDL_MIGRATIONS: list[str] = []  # kept for reference only

_INSERT = """
INSERT INTO clean.annonces
    (titre, prix, prix_type, ville, quartier, surface_m2, nb_chambres,
     nb_salles_bain, etage, lien, scraped_at,
     prix_par_m2, categorie_prix,
     region_label, is_grande_ville)
VALUES %s
ON CONFLICT (lien) DO UPDATE SET
    prix          = EXCLUDED.prix,
    prix_type     = EXCLUDED.prix_type,
    surface_m2    = EXCLUDED.surface_m2,
    nb_chambres   = EXCLUDED.nb_chambres,
    prix_par_m2   = EXCLUDED.prix_par_m2,
    categorie_prix= EXCLUDED.categorie_prix,
    loaded_at     = NOW()
"""

# ── City / region reference ─────────────────────────────────────────────

_VILLE_MAP = {
    "casablanca": "Casablanca", "casa": "Casablanca",
    "rabat": "Rabat",
    "marrakech": "Marrakech", "marrakesh": "Marrakech",
    "fes": "Fès", "fès": "Fès", "fez": "Fès",
    "tanger": "Tanger",
    "agadir": "Agadir",
    "meknes": "Meknès", "meknès": "Meknès",
    "oujda": "Oujda",
    "kenitra": "Kénitra", "kénitra": "Kénitra",
    "tetouan": "Tétouan", "tétouan": "Tétouan",
    "safi": "Safi",
    "mohammedia": "Mohammedia",
    "beni mellal": "Beni Mellal", "béni mellal": "Beni Mellal",
    "el jadida": "El Jadida",
    "nador": "Nador",
    "settat": "Settat",
    "sale": "Salé", "salé": "Salé",
    "temara": "Témara", "témara": "Témara",
    "berrechid": "Berrechid",
    "khouribga": "Khouribga",
    "dakhla": "Dakhla",
    "laayoune": "Laâyoune",
    # ✅ Arabic city name variants found in real data
    "طنجة": "Tanger",
    "مراكش": "Marrakech",
    # FIX #32: Additional Arabic city names found in scraped data.
    # These exist alongside French names — both must normalise to the same
    # canonical form.
    "الدار البيضاء": "Casablanca",
    "الرباط": "Rabat",
    "فاس": "Fès",
    "أكادير": "Agadir",
    "مكناس": "Meknès",
    "وجدة": "Oujda",
    "القنيطرة": "Kénitra",
    "تطوان": "Tétouan",
    "آسفي": "Safi",
    "المحمدية": "Mohammedia",
    "الدار البيضاء": "Casablanca",
    "الرباط": "Rabat",
    "فاس": "Fès",
    "أكادير": "Agadir",
    "مكناس": "Meknès",
    # ✅ FIX: مدن مكتشفة في البيانات الفعلية
    "martil": "Martil",
    "benslimane": "Benslimane",
    "nounous": "Nounous",
    "tamesna": "Tamesna",
    "zenata": "Zenata",
    "sidi bennour": "Sidi Bennour",
}

_REGION_MAP = {
    "Casablanca": "Casablanca-Settat",
    "Mohammedia": "Casablanca-Settat",
    "Berrechid": "Casablanca-Settat",
    "Settat": "Casablanca-Settat",
    "El Jadida": "Casablanca-Settat",
    "Benslimane": "Casablanca-Settat",
    "Zenata": "Casablanca-Settat",
    "Sidi Bennour": "Casablanca-Settat",
    "Rabat": "Rabat-Salé-Kénitra",
    "Salé": "Rabat-Salé-Kénitra",
    "Témara": "Rabat-Salé-Kénitra",
    "Kénitra": "Rabat-Salé-Kénitra",
    "Tamesna": "Rabat-Salé-Kénitra",
    "Marrakech": "Marrakech-Safi",
    "Safi": "Marrakech-Safi",
    "Fès": "Fès-Meknès",
    "Meknès": "Fès-Meknès",
    "Tanger": "Tanger-Tétouan-Al Hoceïma",
    "Tétouan": "Tanger-Tétouan-Al Hoceïma",
    "Martil": "Tanger-Tétouan-Al Hoceïma",
    "Nounous": "Tanger-Tétouan-Al Hoceïma",
    "Agadir": "Souss-Massa",
    "Oujda": "L'Oriental",
    "Nador": "L'Oriental",
    "Beni Mellal": "Béni Mellal-Khénifra",
    "Khouribga": "Béni Mellal-Khénifra",
    "Dakhla": "Dakhla-Oued Ed-Dahab",
    "Laâyoune": "Laâyoune-Sakia El Hamra",
}

# ✅ FIX: قيم quartier بلا معنى تُحوَّل إلى سلسلة فارغة
_QUARTIER_BLACKLIST = {
    "toute la ville", "tout la ville", "autre secteur",
    "toutes les villes", "non précisé",
    "espace de coworking à casablanca",  # not a real quartier
}

# ✅ FIX: quartier normalization map for known duplicates
_QUARTIER_NORMALIZE = {
    "centre ville": "Centre Ville",
    "centre-ville": "Centre Ville",
    "centreville": "Centre Ville",
    "centre": "Centre Ville",
}

# ✅ FIX: حروف جر فرنسية لا تُحوَّل لـ Title Case
_FRENCH_PREPOSITIONS = {
    "de",
    "du",
    "la",
    "le",
    "les",
    "au",
    "aux",
    "en",
    "et",
    "sur"}

# FIX #2: حد أقصى للمساحة في سياق الإيجار السكني.
# 800 م² هو سقف معقول — ما فوقه غالباً خطأ في الـ scraper أو إعلان تجاري.
SURFACE_MAX_RESIDENTIAL = 800  # م²

# FIX #2: حد أدنى للسعر الشهري حسب نوع المدينة.
# إذا كان السعر أقل من هذا الحد لمدينة كبيرة → على الأرجح إيجار يومي مصنّف خطأً.
# المبدأ: إيجار أقل من 1,000 DH/شهر في Casablanca أو Rabat = مستحيل سوقياً.
# DH — للمدن الكبيرة (Casablanca, Rabat, Tanger...)
_PRIX_MENSUEL_MIN_GRANDE_VILLE = 1_000
# DH — للمدن الصغيرة والمناطق السياحية (Saidia, Martil...)
_PRIX_MENSUEL_MIN_PETITE_VILLE = 400

# FIX #2: تصنيف الإيجار الشهري (مختلف كلياً عن تصنيف البيع)
_PRIX_SEUILS_LOCATION_MENSUEL = [
    (3_000, "Très Bas"),
    (6_000, "Bas"),
    (12_000, "Moyen"),
    (25_000, "Élevé"),
    (float("inf"), "Luxe"),
]

# FIX #4: سلّم منفصل للإيجار اليومي — مختلف تماماً عن الشهري
# 500 DH/ليلة ليس "Très Bas" — هو "Moyen" في سياق الإيجار اليومي
_PRIX_SEUILS_LOCATION_JOURNALIER = [
    (200, "Très Bas"),
    (400, "Bas"),
    (800, "Moyen"),
    (1_500, "Élevé"),
    (float("inf"), "Luxe"),
]

_PRIX_SEUILS_VENTE = [
    (300_000, "Bas"),
    (800_000, "Moyen"),
    (2_000_000, "Élevé"),
    (float("inf"), "Luxe"),
]


# ── Parsing helpers ─────────────────────────────────────────────────────

def _extract_number(text) -> float | None:
    if not isinstance(text, str):
        return None
    text = (
        text.replace("\u202f", "")
            .replace("\xa0", "")
            .replace(" ", "")
            .replace(",", ".")
    )
    m = re.search(r"\d+\.?\d*", text)
    return float(m.group()) if m else None


def _clean_prix(v) -> float | None:
    return _extract_number(str(v)) if pd.notna(v) else None


def _clean_surface(v) -> float | None:
    return _extract_number(str(v)) if pd.notna(v) else None


def _clean_int(v, max_val: int = 32767) -> int | None:
    n = _extract_number(str(v)) if pd.notna(v) else None
    if n is None:
        return None
    n = int(n)
    # ✅ FIX: 0 يُعامل كـ None (لا يوجد 0 حمام أو 0 غرفة)
    if n <= 0 or n > max_val:
        return None
    return n


def _standardize_ville(v) -> str:
    if not isinstance(v, str):
        return ""
    return _VILLE_MAP.get(v.strip().lower(), v.strip().title())


def _smart_title(text: str) -> str:
    """
    ✅ FIX: title case ذكي يحترم حروف الجر الفرنسية.
    'du Golf' يبقى 'du Golf' وليس 'Du Golf'.
    """
    if not text:
        return text
    words = text.split()
    result = []
    for i, word in enumerate(words):
        if i == 0:
            result.append(word.capitalize())
        elif word.lower() in _FRENCH_PREPOSITIONS:
            result.append(word.lower())
        else:
            result.append(word.capitalize())
    return " ".join(result)


def _clean_quartier(v: str) -> str:
    """✅ FIX: حذف القيم بلا معنى + normalization + title case ذكي."""
    if not isinstance(v, str):
        return ""
    v = v.strip()
    if v.lower() in _QUARTIER_BLACKLIST:
        return ""
    # Normalize known duplicate spellings
    normalized = _QUARTIER_NORMALIZE.get(v.lower())
    if normalized:
        return normalized
    return _smart_title(v)


def _detect_daily_rental(
        prix: float | None,
        prix_type: str,
        ville: str) -> str:
    """
    FIX #2: كشف الإيجارات اليومية المصنّفة خطأً كشهرية.

    المنطق:
      - إذا كان prix_type بالفعل 'journalier' أو 'inconnu' → لا تغيير.
      - إذا كان prix_type = 'mensuel' لكن السعر أقل من الحد الأدنى المنطقي
        للمدينة → نصنّفه كـ 'journalier_suspect' حتى لا يدخل في تحليل الإيجار الشهري.

    القاعدة:
      - المدن الكبيرة  : أقل من 1,000 DH/شهر = مشبوه
      - المدن الصغيرة  : أقل من   400 DH/شهر = مشبوه
    """
    if prix_type != "mensuel" or prix is None:
        return prix_type

    is_grande = ville in _GRANDES_VILLES
    threshold = _PRIX_MENSUEL_MIN_GRANDE_VILLE if is_grande else _PRIX_MENSUEL_MIN_PETITE_VILLE

    if prix < threshold:
        logger.warning(
            f"FIX #2 — Prix suspect détecté: {prix} DH/mois à {ville} "
            f"(seuil min = {threshold} DH) → reclassifié comme 'journalier_suspect'.")
        return "journalier_suspect"

    return prix_type


def _categorize_prix(prix, prix_type: str = "mensuel") -> str:
    """FIX #4: سلّم منفصل لكل نوع إيجار: mensuel / journalier / vente."""
    if prix is None or (isinstance(prix, float) and np.isnan(prix)):
        return "Inconnu"
    if prix_type == "journalier":
        seuils = _PRIX_SEUILS_LOCATION_JOURNALIER
    elif prix_type in ("mensuel", "journalier_suspect"):
        seuils = _PRIX_SEUILS_LOCATION_MENSUEL
    else:
        seuils = _PRIX_SEUILS_VENTE
    for seuil, label in seuils:
        if prix < seuil:
            return label
    return "Luxe"


# ── Pipeline ────────────────────────────────────────────────────────────

def _fetch_staging(run_id: str | None = None) -> pd.DataFrame:
    """
    FIX #15: When run_id is given, only fetch rows from that specific run.
    This prevents data mixing when staging accumulates rows across retried runs.
    Falls back to DISTINCT ON (lien) dedup when run_id is not available.
    """
    conn = get_connection()
    try:
        if run_id:
            df = pd.read_sql(
                """
                SELECT * FROM staging.raw_annonces
                WHERE run_id = %(run_id)s
                ORDER BY loaded_at DESC
                """,
                conn,
                params={"run_id": run_id},
            )
            logger.info(
                f"Fetched {len(df)} rows from staging (run_id={run_id}).")
        else:
            df = pd.read_sql(
                """
                SELECT DISTINCT ON (lien) *
                FROM staging.raw_annonces
                ORDER BY lien, loaded_at DESC
                """,
                conn,
            )
            logger.info(
                f"Fetched {len(df)} unique rows from staging (deduped by lien, no run_id filter).")
        return df
    finally:
        release_connection(conn)


def _apply_missing_value_strategy(df: pd.DataFrame) -> pd.DataFrame:
    n0 = len(df)
    # ✅ FIX: حذف السطور بسعر = 0 أو سالب أو منخفض جداً (أقل من 200 DH شهرياً)
    # السعر 25 DH و499 DH هما أخطاء في الـ scraper، ليسا إيجارات حقيقية
    PRIX_MIN_THRESHOLD = 200
    df = df[df["prix"].notna() & (df["prix"] >= PRIX_MIN_THRESHOLD)]
    logger.info(
        f"Missing-value strategy: dropped {n0 - len(df)} rows with null/zero/implausibly-low prix (< {PRIX_MIN_THRESHOLD} DH)")  # noqa: E501

    # FIX #35: Filter out sale listings masquerading as rental data.
    # A monthly rental above 150,000 DH is implausible for residential property;
    # values like 1,287,000 DH on a 99m² apartment are "mabni lil bai3" (for sale)
    # scraped from the rental URL due to scraper miscategorisation.
    # We drop rows where prix_type='mensuel' but prix > RENTAL_MAX_DH.
    RENTAL_MAX_DH = 150_000
    if "prix_type" in df.columns:
        sale_mask = (
            df["prix_type"] == "mensuel") & (
            df["prix"] > RENTAL_MAX_DH)
        n_sale = sale_mask.sum()
        if n_sale:
            logger.warning(
                f"Missing-value strategy: dropped {n_sale} rows with suspiciously high "
                f"rental price (> {RENTAL_MAX_DH:,} DH) — likely sale listings mis-scraped as rental.")  # noqa: E501
            df = df[~sale_mask]
    n1 = len(df)
    _ARTEFACT_VILLES = {
        "COURS ET FORMATIONS", "Cours Et Formations", "cours et formations",
        "STAGES", "Stages",
    }
    df = df[
        df["ville"].notna() &
        (df["ville"].str.strip() != "") &
        (~df["ville"].str.strip().isin(_ARTEFACT_VILLES))
    ]
    logger.info(
        f"Missing-value strategy: dropped {n1 - len(df)} rows with empty/artefact ville")
    df["etage"] = df["etage"].fillna("Non précisé").replace("", "Non précisé")
    df["titre"] = df["titre"].fillna("Sans titre").replace("", "Sans titre")
    df["quartier"] = df["quartier"].fillna("")
    df["scraped_at"] = df["scraped_at"].fillna(pd.Timestamp.utcnow())
    # ✅ FIX: nb_salles_bain = 0 → None (معالَج في _clean_int لكن نضمن هنا)
    if "nb_salles_bain" in df.columns:
        df["nb_salles_bain"] = df["nb_salles_bain"].apply(
            lambda x: None if x == 0 else x
        )
    logger.info(f"Missing-value strategy applied. Remaining rows: {len(df)}")

    # FIX #3: حذف السجلات بمساحة غير منطقية في سياق الإيجار السكني.
    # 800 م² سقف معقول — ما فوقه غالباً خطأ في الـ scraper أو إعلان
    # تجاري/صناعي.
    if "surface_m2" in df.columns:
        n_before = len(df)
        oversized = df["surface_m2"].notna() & (
            df["surface_m2"] > SURFACE_MAX_RESIDENTIAL)
        if oversized.sum():
            logger.warning(
                f"FIX #3 — {oversized.sum()} سجل(ات) بمساحة > {SURFACE_MAX_RESIDENTIAL} م² "
                "تم وضع علامة عليها: "
                + ", ".join(
                    f"{r['surface_m2']}م² ({r['ville']})"
                    for _, r in df[oversized][["surface_m2", "ville"]].iterrows()
                )
            )
            df = df[~oversized]
            logger.info(
                f"FIX #3 — surface_m2 cap: حُذف {n_before - len(df)} سجل(ات) "
                f"(surface_m2 > {SURFACE_MAX_RESIDENTIAL} م²)."
            )

    # FIX #4: etage = "0" مشبوه — يعني الـ scraper ما جمعه، ليس RDC حقيقي.
    # نحوّله لـ "Non précisé" مثلما نفعل مع nb_chambres وnb_salles_bain.
    if "etage" in df.columns:
        df["etage"] = df["etage"].replace("0", "Non précisé")

    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    n0 = len(df)
    df = df.drop_duplicates(subset=["lien"], keep="last")
    logger.info(f"Dedup: {n0} → {len(df)} rows ({n0 - len(df)} removed)")

    df["prix"] = df["prix"].apply(_clean_prix)
    df["surface_m2"] = df["surface"].apply(_clean_surface)
    df["nb_chambres"] = df["nb_chambres"].apply(_clean_int)
    df["nb_salles_bain"] = df["nb_salles_bain"].apply(_clean_int)

    df["ville"] = df["ville"].apply(_standardize_ville)
    # ✅ FIX: quartier ذكي — يحذف القيم بلا معنى ويحترم حروف الجر
    df["quartier"] = df["quartier"].fillna("").apply(_clean_quartier)
    df["titre"] = df["titre"].fillna("").str.strip()
    # FIX #4: etage = "0" → "Non précisé"
    # الـ scraper يستخرج "0" عندما لا يجد قيمة — ليس طابق أرضي حقيقي.
    # نعالجه هنا قبل _apply_missing_value_strategy حتى لا يمر كقيمة صحيحة.
    df["etage"] = (
        df["etage"]
        .fillna("")
        .str.strip()
        .replace({"0": "Non précisé", "": "Non précisé"})
    )
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")

    # ✅ FIX: prix_type — أضف العمود إن لم يكن موجوداً (توافق مع staging القديمة)
    if "prix_type" not in df.columns:
        df["prix_type"] = "mensuel"
    else:
        df["prix_type"] = df["prix_type"].fillna("mensuel")

    # FIX #2: كشف الإيجارات اليومية المصنّفة خطأً كشهرية — يجب أن يكون بعد
    # _clean_prix و_standardize_ville حتى تكون القيم الرقمية والمدن جاهزة.
    df["prix_type"] = df.apply(lambda row: _detect_daily_rental(
        row["prix"], row["prix_type"], row["ville"]), axis=1, )
    n_suspect = (df["prix_type"] == "journalier_suspect").sum()
    if n_suspect:
        logger.warning(
            f"FIX #2 — {n_suspect} سجل(ات) أُعيد تصنيفها كـ 'journalier_suspect' "
            "بسبب سعر شهري غير منطقي.")

    df = _apply_missing_value_strategy(df)

    MIN_ROWS_FOR_OUTLIER = 30
    for col in ["prix", "surface_m2"]:
        n_valid = df[col].notna().sum()
        if n_valid < MIN_ROWS_FOR_OUTLIER:
            logger.warning(
                f"Outlier filter [{col}] skipped — only {n_valid} non-null rows "
                f"(minimum required: {MIN_ROWS_FOR_OUTLIER}). "
                "Collect more data before relying on quantile filtering.")
            continue
        q_lo = df[col].quantile(0.01)
        q_hi = df[col].quantile(0.99)
        n_before = len(df)
        df = df[df[col].isna() | ((df[col] >= q_lo) & (df[col] <= q_hi))]
        logger.info(
            f"Outlier filter [{col}]: removed {n_before - len(df)} rows")

    df["prix_par_m2"] = np.where(
        df["surface_m2"].notna() & (df["surface_m2"] > 0) & df["prix"].notna(),
        (df["prix"] / df["surface_m2"]).round(2),
        np.nan,
    )

    # FIX #3: Log surface_m2 fill rate — prix_par_m2 is only as good as
    # surface coverage.
    n_total = len(df)
    n_surface = df["surface_m2"].notna().sum()
    n_ppm2 = df["prix_par_m2"].notna().sum()
    surface_pct = (n_surface / n_total * 100) if n_total else 0
    ppm2_pct = (n_ppm2 / n_total * 100) if n_total else 0
    surface_status = "✅" if surface_pct >= 60 else (
        "⚠️" if surface_pct >= 30 else "❌")
    logger.info(
        f"FIX #3 — surface_m2 fill: {surface_status} {n_surface}/{n_total} ({surface_pct:.1f}%) "
        f"→ prix_par_m2 computable for {n_ppm2}/{n_total} rows ({ppm2_pct:.1f}%)")
    if surface_pct < 30:
        logger.warning(
            "FIX #3 — surface_m2 fill rate is critically low (<30%). "
            "prix_par_m2 will be unreliable for BI and ML. "
            "Consider improving surface extraction in the scraper."
        )

    # ✅ FIX: تصنيف منفصل للإيجار vs البيع
    df["categorie_prix"] = df.apply(
        lambda row: _categorize_prix(
            row["prix"], row.get(
                "prix_type", "mensuel")), axis=1, )

    df["region_label"] = df["ville"].map(_REGION_MAP).fillna("Autre")
    df["is_grande_ville"] = df["ville"].isin(_GRANDES_VILLES)

    # ✅ FIX: حذف عمود surface الخام من الـ DataFrame النهائي
    if "surface" in df.columns:
        df = df.drop(columns=["surface"])

    logger.info(f"Cleaning done. Shape: {df.shape}")
    return df


def _ml_readiness_report(df: pd.DataFrame) -> None:
    feature_cols = [
        "prix", "prix_type", "surface_m2", "nb_chambres", "nb_salles_bain",
        "etage", "ville", "quartier",
        "prix_par_m2", "categorie_prix",
        "region_label", "is_grande_ville",
    ]
    n = len(df)
    if n == 0:
        logger.warning("ML Readiness: 0 rows — cannot compute.")
        return
    lines = [f"\n🤖 ML READINESS REPORT — {n} rows"]
    for col in feature_cols:
        if col not in df.columns:
            lines.append(f"  ❓ {col:<22}: column not found")
            continue
        null_count = df[col].isna().sum()
        fill_pct = 100 * (n - null_count) // n
        status = "✅" if fill_pct >= 80 else ("⚠️" if fill_pct >= 40 else "❌")
        lines.append(
            f"  {status} {col:<22}: {n - null_count}/{n} filled ({fill_pct}%)")
    logger.info("\n".join(lines))


def _save_silver(df: pd.DataFrame) -> None:
    """
    Save silver data partitioned by date only:
      data/silver/YYYY/MM/DD/avito_clean_<ts>.csv
      data/silver/YYYY/MM/DD/avito_clean_<ts>.parquet
    """
    if df.empty:
        logger.warning("Silver: DataFrame is empty — nothing to save.")
        return

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    date_pfx = datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
    part_dir = os.path.join(SILVER_DIR, date_pfx)
    os.makedirs(part_dir, exist_ok=True)

    stem = f"avito_clean_{ts}"

    # CSV
    csv_path = os.path.join(part_dir, f"{stem}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8")
    logger.info(f"Silver CSV     → {csv_path}  ({len(df)} rows)")

    # Parquet
    parquet_path = os.path.join(part_dir, f"{stem}.parquet")
    try:
        df.to_parquet(parquet_path, index=False, engine="pyarrow")
        logger.info(f"Silver Parquet → {parquet_path}  ({len(df)} rows)")
    except Exception as e:
        logger.warning(f"Silver Parquet skipped: {e}")


def _load_to_db(df: pd.DataFrame) -> None:
    execute_query(_DDL_SCHEMA)
    execute_query(_DDL_TABLE)
    # FIX #14: Migrations removed — handled centrally by run_all_migrations()
    # in pipeline.py.

    cols = [
        "titre", "prix", "prix_type", "ville", "quartier", "surface_m2",
        "nb_chambres", "nb_salles_bain", "etage",
        "lien", "scraped_at", "prix_par_m2", "categorie_prix",
        "region_label", "is_grande_ville",
    ]

    missing = [c for c in cols if c not in df.columns]
    if missing:
        logger.error(f"Missing columns in DataFrame: {missing}")
        return

    sub = df[cols].where(pd.notna(df[cols]), other=float("nan"))
    INT_COLS = {'nb_chambres', 'nb_salles_bain'}

    def safe_row(row: tuple) -> list:
        result = []
        for col, val in zip(cols, row):
            if col in INT_COLS and val is not None:
                try:
                    v = int(val)
                    result.append(v if 0 < v <= 32767 else None)
                except (ValueError, TypeError):
                    result.append(None)
            else:
                result.append(val)
        return result

    rows = [safe_row(r) for r in sub.itertuples(index=False, name=None)]
    bulk_insert(_INSERT, rows)


def run_clean(run_id: str | None = None) -> pd.DataFrame:
    """
    FIX #15: Accepts run_id to filter staging data by the current pipeline run.
    When provided, only rows with matching run_id are cleaned — preventing
    data mixing from previous partial/failed runs.
    """
    logger.info("=== Clean layer started ===")
    df_raw = _fetch_staging(run_id=run_id)

    # ── Pre-clean validation ─────────────────────────────────────────────────
    # Hard failures raise CleanValidationError and abort the pipeline.
    try:
        validate_pre_clean(df_raw)
    except CleanValidationError as e:
        logger.critical(f"❌ PRE-CLEAN VALIDATION FAILED: {e}")
        raise

    n_staging = len(df_raw)
    df_clean = _clean(df_raw)

    # ── Post-clean validation ────────────────────────────────────────────────
    # Runs after transformation — gates the DB write and silver export.
    try:
        validate_post_clean(df_clean, n_staging)
    except CleanValidationError as e:
        logger.critical(f"❌ POST-CLEAN VALIDATION FAILED: {e}")
        raise

    _ml_readiness_report(df_clean)
    _save_silver(df_clean)
    _load_to_db(df_clean)
    logger.info("=== Clean layer finished ===")
    return df_clean
