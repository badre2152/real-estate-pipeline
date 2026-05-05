"""
BI Schema — Star Schema for Power BI / reporting.

FIX #11: Replaced iterrows (slow, creates Series per row) with
         df.itertuples() which is 3-5× faster and avoids Series overhead.
FIX #14: _DDL_MIGRATIONS removed — now handled centrally in utils/migrations.py.
FIX #53: Uses connection pool via get_connection() / release_connection().
"""

import os
import numpy as np
import pandas as pd
from datetime import datetime

from src.utils.db import get_connection, release_connection, execute_query, fetch_all
from src.utils.logger import get_logger

logger = get_logger("bi_schema")

GOLD_BI_DIR = os.path.join(os.path.dirname(__file__), "../../data/gold/bi")

_DDL = [
    "CREATE SCHEMA IF NOT EXISTS bi_schema;",

    """CREATE TABLE IF NOT EXISTS bi_schema.dim_localisation (
        id_localisation SERIAL PRIMARY KEY,
        ville           TEXT NOT NULL,
        quartier        TEXT NOT NULL DEFAULT '',
        region_label    TEXT NOT NULL DEFAULT 'Autre',
        is_grande_ville BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE (ville, quartier)
    );""",

    """CREATE TABLE IF NOT EXISTS bi_schema.dim_caracteristiques (
        id_caracteristiques SERIAL PRIMARY KEY,
        nb_chambres         BIGINT,
        nb_salles_bain      BIGINT,
        etage               TEXT NOT NULL DEFAULT '',
        annee_construction  BIGINT,
        age_bien            BIGINT
    );""",

    """CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_car_unique
        ON bi_schema.dim_caracteristiques (
            COALESCE(nb_chambres, -1),
            COALESCE(nb_salles_bain, -1),
            etage,
            COALESCE(annee_construction, -1)
        );""",

    """CREATE TABLE IF NOT EXISTS bi_schema.dim_temps (
        id_temps     SERIAL PRIMARY KEY,
        date_jour    DATE NOT NULL UNIQUE,
        annee        INTEGER,
        trimestre    INTEGER,
        mois         INTEGER,
        jour         INTEGER,
        jour_semaine INTEGER
    );""",

    """CREATE TABLE IF NOT EXISTS bi_schema.fact_annonce (
        id_annonce          SERIAL PRIMARY KEY,
        id_localisation     INTEGER REFERENCES bi_schema.dim_localisation(id_localisation),
        id_caracteristiques INTEGER REFERENCES bi_schema.dim_caracteristiques(id_caracteristiques),
        id_temps            INTEGER REFERENCES bi_schema.dim_temps(id_temps),
        titre               TEXT,
        prix                NUMERIC,
        prix_type           TEXT DEFAULT 'mensuel',
        surface_m2          NUMERIC,
        prix_par_m2         NUMERIC,
        categorie_prix      TEXT,
        lien                TEXT UNIQUE,
        loaded_at           TIMESTAMP DEFAULT NOW()
    );""",

    "CREATE INDEX IF NOT EXISTS idx_fact_loc  ON bi_schema.fact_annonce(id_localisation);",
    "CREATE INDEX IF NOT EXISTS idx_fact_car  ON bi_schema.fact_annonce(id_caracteristiques);",
    "CREATE INDEX IF NOT EXISTS idx_fact_time ON bi_schema.fact_annonce(id_temps);",
    "CREATE INDEX IF NOT EXISTS idx_fact_prix ON bi_schema.fact_annonce(prix);",
    "CREATE INDEX IF NOT EXISTS idx_fact_type ON bi_schema.fact_annonce(prix_type);",
]

_VIEWS = [
    # FIX #4: Exclude journalier_suspect from both views.
    # journalier_suspect = mensuel price that is implausibly low for its city.
    # Including them in BI averages skews price-per-city metrics downward.
    """CREATE OR REPLACE VIEW bi_schema.v_annonces_full AS
    SELECT
        f.id_annonce,
        l.ville, l.quartier, l.region_label, l.is_grande_ville,
        c.nb_chambres, c.nb_salles_bain, c.etage,
        c.annee_construction, c.age_bien,
        t.date_jour, t.annee, t.trimestre, t.mois,
        f.titre, f.prix, f.prix_type, f.surface_m2, f.prix_par_m2,
        f.categorie_prix, f.lien
    FROM bi_schema.fact_annonce f
    LEFT JOIN bi_schema.dim_localisation     l ON f.id_localisation     = l.id_localisation
    LEFT JOIN bi_schema.dim_caracteristiques c ON f.id_caracteristiques = c.id_caracteristiques
    LEFT JOIN bi_schema.dim_temps            t ON f.id_temps            = t.id_temps
    WHERE f.prix_type != 'journalier_suspect';
    """,
    """CREATE OR REPLACE VIEW bi_schema.v_prix_par_ville AS
    SELECT
        l.ville,
        l.region_label,
        f.prix_type,
        COUNT(*)                              AS nb_annonces,
        ROUND(AVG(f.prix)::numeric, 0)        AS prix_moyen,
        ROUND(AVG(f.prix_par_m2)::numeric, 0) AS prix_m2_moyen,
        MIN(f.prix)                           AS prix_min,
        MAX(f.prix)                           AS prix_max
    FROM bi_schema.fact_annonce f
    JOIN bi_schema.dim_localisation l ON f.id_localisation = l.id_localisation
    WHERE f.prix IS NOT NULL
      AND f.prix_type != 'journalier_suspect'
    GROUP BY l.ville, l.region_label, f.prix_type
    ORDER BY prix_moyen DESC;
    """,
]


def _upsert_localisation(cur, ville, quartier, region_label, is_grande_ville) -> int:
    cur.execute(
        """
        INSERT INTO bi_schema.dim_localisation
            (ville, quartier, region_label, is_grande_ville)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (ville, quartier)
        DO UPDATE SET
            region_label    = EXCLUDED.region_label,
            is_grande_ville = EXCLUDED.is_grande_ville
        RETURNING id_localisation
        """,
        (ville or "", quartier or "", region_label or "Autre", bool(is_grande_ville)),
    )
    return cur.fetchone()[0]


def _safe_int(v, default=None):
    if v is None or (isinstance(v, float) and v != v):
        return default
    try:
        return int(v)
    except (ValueError, TypeError, OverflowError):
        return default


def _upsert_caracteristiques(cur, nb_ch, nb_sb, etage, annee, age) -> int:
    nb_ch = _safe_int(nb_ch)
    nb_sb = _safe_int(nb_sb)
    annee = _safe_int(annee)
    age   = None if (age is None or (isinstance(age, float) and age != age)) else int(age)
    cur.execute(
        """
        INSERT INTO bi_schema.dim_caracteristiques
            (nb_chambres, nb_salles_bain, etage, annee_construction, age_bien)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (
            COALESCE(nb_chambres, -1),
            COALESCE(nb_salles_bain, -1),
            etage,
            COALESCE(annee_construction, -1)
        )
        DO UPDATE SET age_bien = EXCLUDED.age_bien
        RETURNING id_caracteristiques
        """,
        (nb_ch, nb_sb, etage or "", annee, age),
    )
    return cur.fetchone()[0]


def _upsert_temps(cur, scraped_at) -> int:
    if scraped_at is None or (isinstance(scraped_at, float) and np.isnan(scraped_at)):
        d = datetime.utcnow().date()
    elif isinstance(scraped_at, datetime):
        d = scraped_at.date()
    elif isinstance(scraped_at, str):
        d = datetime.fromisoformat(scraped_at).date()
    else:
        d = datetime.utcnow().date()

    cur.execute(
        """
        INSERT INTO bi_schema.dim_temps
            (date_jour, annee, trimestre, mois, jour, jour_semaine)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (date_jour) DO NOTHING
        RETURNING id_temps
        """,
        (d, d.year, (d.month - 1) // 3 + 1, d.month, d.day, d.weekday()),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("SELECT id_temps FROM bi_schema.dim_temps WHERE date_jour = %s", (d,))
    return cur.fetchone()[0]


def _validate(inserted_this_run: int):
    logger.info("── Post-load BI validation starting ──")
    warnings = 0

    rows = fetch_all("SELECT COUNT(*) FROM bi_schema.fact_annonce;")
    total_in_db = rows[0][0] if rows else 0
    logger.info(
        f"Validation ℹ fact_annonce: {inserted_this_run} inserted this run "
        f"| {total_in_db} total in DB"
    )

    for dim, col in [("dim_localisation", "id_localisation"),
                     ("dim_caracteristiques", "id_caracteristiques")]:
        rows = fetch_all(f"""
            SELECT COUNT(*) FROM bi_schema.fact_annonce f
            WHERE f.{col} IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM bi_schema.{dim} d
                  WHERE d.{col} = f.{col}
              );
        """)
        orphans = rows[0][0] if rows else 0
        if orphans:
            logger.warning(f"Validation ❌ {orphans} orphan rows ({dim})")
            warnings += 1
        else:
            logger.info(f"Validation ✅ {dim} FK: no orphans")

    if warnings == 0:
        logger.info("── Post-load BI validation PASSED ✅ ──")
    else:
        logger.warning(f"── Post-load BI validation finished with {warnings} warning(s) ──")


def _fetch_clean() -> pd.DataFrame:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM clean.annonces")
        rows = cur.fetchall()
        cols = [desc[0] for desc in (cur.description or [])]
        cur.close()
        return pd.DataFrame(rows, columns=cols)
    finally:
        release_connection(conn)


def run_bi_schema(df: pd.DataFrame | None = None):
    logger.info("=== BI Schema load started ===")

    for stmt in _DDL:
        execute_query(stmt)

    # FIX #14: Migrations now applied centrally in utils/migrations.py.
    # run_bi_schema() only creates base tables; the pipeline calls
    # run_all_migrations() once before any schema function.

    logger.info("BI Schema DDL applied.")

    if df is None:
        df = _fetch_clean()
        logger.info(f"Loaded {len(df)} rows from clean.annonces")

    if "prix_type" not in df.columns:
        df = df.copy()
        df["prix_type"] = "mensuel"

    conn    = get_connection()
    count   = 0
    skipped = 0

    def _val(v):
        return None if pd.isna(v) else v

    # FIX #11: Use itertuples instead of iterrows.
    # iterrows() creates a full Series per row (slow + dtype coercion).
    # itertuples() yields a lightweight namedtuple — 3-5× faster.
    # We access fields by attribute name; .get() replaced by getattr with default.
    rows_iter = df.reset_index(drop=True).itertuples(index=True, name="Row")

    try:
        with conn:
            cur = conn.cursor()

            for row in rows_iter:
                i = row.Index
                savepoint = f"sp_row_{i}"

                def _g(field, default=None):
                    v = getattr(row, field, default)
                    return default if (v is None or (isinstance(v, float) and v != v)) else v

                try:
                    cur.execute(f"SAVEPOINT {savepoint}")

                    id_loc = _upsert_localisation(
                        cur,
                        _g("ville", ""),
                        _g("quartier", ""),
                        _g("region_label", "Autre"),
                        _g("is_grande_ville", False),
                    )
                    id_car = _upsert_caracteristiques(
                        cur,
                        _g("nb_chambres"),
                        _g("nb_salles_bain"),
                        _g("etage", ""),
                        _g("annee_construction"),
                        _g("age_bien"),
                    )
                    id_tps = _upsert_temps(cur, getattr(row, "scraped_at", None))

                    cur.execute(
                        """
                        INSERT INTO bi_schema.fact_annonce
                            (id_localisation, id_caracteristiques, id_temps,
                             titre, prix, prix_type, surface_m2, prix_par_m2,
                             categorie_prix, lien)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (lien) DO NOTHING
                        """,
                        (
                            id_loc, id_car, id_tps,
                            _g("titre"),
                            _val(getattr(row, "prix", None)),
                            _g("prix_type", "mensuel"),
                            _val(getattr(row, "surface_m2", None)),
                            _val(getattr(row, "prix_par_m2", None)),
                            _g("categorie_prix"),
                            _g("lien"),
                        ),
                    )

                    cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                    count += 1

                except Exception as e:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                    skipped += 1
                    logger.warning(f"Row {i} skipped — rolled back cleanly: {e}")

            cur.close()

    finally:
        release_connection(conn)

    logger.info(
        f"=== BI Schema load finished — {count} inserted, {skipped} skipped ==="
    )

    views_ok = 0
    for view_sql in _VIEWS:
        try:
            execute_query(view_sql)
            views_ok += 1
        except Exception as e:
            logger.warning(f"Could not create view: {e}")
    if views_ok == len(_VIEWS):
        logger.info("Power BI helper views created successfully.")
    else:
        logger.warning(f"Only {views_ok}/{len(_VIEWS)} views created — check warnings above.")

    _validate(inserted_this_run=count)
    _save_gold_bi()


def _save_gold_bi():
    """
    Export BI gold layer to data/gold/bi/.
    Exports:
      - annonces_full_TIMESTAMP.csv   → full denormalized view (v_annonces_full)
      - prix_par_ville_TIMESTAMP.csv  → aggregated price stats per city (v_prix_par_ville)
    """
    os.makedirs(GOLD_BI_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    exports = [
        ("SELECT * FROM bi_schema.v_annonces_full;",  f"annonces_full_{ts}.csv"),
        ("SELECT * FROM bi_schema.v_prix_par_ville;", f"prix_par_ville_{ts}.csv"),
    ]

    conn = get_connection()
    try:
        for sql, filename in exports:
            try:
                df = pd.read_sql(sql, conn)
                path = os.path.join(GOLD_BI_DIR, filename)
                df.to_csv(path, index=False, encoding="utf-8")
                logger.info(f"Gold BI → {path}  ({len(df)} rows)")
            except Exception as e:
                logger.warning(f"Gold BI export failed for {filename}: {e}")
    finally:
        release_connection(conn)