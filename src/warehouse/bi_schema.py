"""Build and export the Power BI reporting schema."""

import os
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Any, Optional

import psycopg2.extensions

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
        quartier_known  BOOLEAN NOT NULL DEFAULT FALSE,
        region_label    TEXT NOT NULL DEFAULT 'Autre',
        is_grande_ville BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE (ville, quartier)
    );""",

    """CREATE TABLE IF NOT EXISTS bi_schema.dim_caracteristiques (
        id_caracteristiques SERIAL PRIMARY KEY,
        nb_chambres         BIGINT,
        nb_salles_bain      BIGINT,
        etage               TEXT NOT NULL DEFAULT ''
    );""",

    """CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_car_unique
        ON bi_schema.dim_caracteristiques (
            COALESCE(nb_chambres, -1),
            COALESCE(nb_salles_bain, -1),
            etage
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
    """CREATE OR REPLACE VIEW bi_schema.v_annonces_full AS
    SELECT
        f.id_annonce,
        l.ville, l.quartier, l.quartier_known, l.region_label, l.is_grande_ville,
        c.nb_chambres, c.nb_salles_bain, c.etage,
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
    """CREATE OR REPLACE VIEW bi_schema.v_prix_par_quartier AS
    SELECT
        l.ville,
        l.quartier,
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
      AND l.quartier_known = TRUE
    GROUP BY l.ville, l.quartier, l.region_label, f.prix_type
    HAVING COUNT(*) >= 3
    ORDER BY l.ville, prix_moyen DESC;
    """,
]


def _upsert_localisation(
        cur: psycopg2.extensions.cursor,
        ville: str,
        quartier: str | None,
        region_label: str,
        is_grande_ville: bool) -> int:
    quartier_val = quartier or ""
    quartier_known = bool(quartier_val.strip())
    cur.execute(
        """
        INSERT INTO bi_schema.dim_localisation
            (ville, quartier, quartier_known, region_label, is_grande_ville)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (ville, quartier)
        DO UPDATE SET
            quartier_known  = EXCLUDED.quartier_known,
            region_label    = EXCLUDED.region_label,
            is_grande_ville = EXCLUDED.is_grande_ville
        RETURNING id_localisation
        """,
        (ville or "",
         quartier_val,
         quartier_known,
         region_label or "Autre",
         bool(is_grande_ville)),
    )
    row = cur.fetchone()
    return row[0] if row is not None else 0


def _safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    if v is None or (isinstance(v, float) and v != v):
        return default
    try:
        return int(v)
    except (ValueError, TypeError, OverflowError):
        return default


def _upsert_caracteristiques(
        cur: psycopg2.extensions.cursor,
        nb_ch: int | None,
        nb_sb: int | None,
        etage: int | str | None) -> int:
    nb_ch = _safe_int(nb_ch)
    nb_sb = _safe_int(nb_sb)
    cur.execute(
        """
        INSERT INTO bi_schema.dim_caracteristiques
            (nb_chambres, nb_salles_bain, etage)
        VALUES (%s, %s, %s)
        ON CONFLICT (
            COALESCE(nb_chambres, -1),
            COALESCE(nb_salles_bain, -1),
            etage
        )
        DO NOTHING
        RETURNING id_caracteristiques
        """,
        (nb_ch, nb_sb, etage or ""),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        """
        SELECT id_caracteristiques FROM bi_schema.dim_caracteristiques
        WHERE COALESCE(nb_chambres, -1) = COALESCE(%s, -1)
          AND COALESCE(nb_salles_bain, -1) = COALESCE(%s, -1)
          AND etage = %s
        """,
        (nb_ch, nb_sb, etage or ""),
    )
    row = cur.fetchone()
    return row[0] if row is not None else 0


def _upsert_temps(cur: psycopg2.extensions.cursor, scraped_at: str) -> int:
    if scraped_at is None or (
        isinstance(
            scraped_at,
            float) and np.isnan(scraped_at)):
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
    cur.execute(
        "SELECT id_temps FROM bi_schema.dim_temps WHERE date_jour = %s", (d,))
    row = cur.fetchone()
    return row[0] if row is not None else 0


def _validate(inserted_this_run: int) -> None:
    logger.info("BI validation started")
    warnings = 0

    rows = fetch_all("SELECT COUNT(*) FROM bi_schema.fact_annonce;")
    total_in_db = rows[0][0] if rows else 0
    logger.info(
        f"BI validation: {inserted_this_run} rows processed "
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
            logger.warning(f"BI validation: {orphans} orphan rows in {dim}")
            warnings += 1
        else:
            logger.info(f"BI validation: no orphan rows in {dim}")

    if warnings:
        raise RuntimeError(f"BI validation failed: {warnings} invalid dimensions")
    logger.info("BI validation passed")


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


def run_bi_schema(df: pd.DataFrame | None = None) -> None:
    logger.info("=== BI Schema load started ===")

    for stmt in _DDL:
        execute_query(stmt)


    logger.info("BI Schema DDL applied.")

    if df is None:
        df = _fetch_clean()
        logger.info(f"Loaded {len(df)} rows from clean.annonces")

    if "prix_type" not in df.columns:
        df = df.copy()
        df["prix_type"] = "mensuel"

    conn = get_connection()
    count = 0
    skipped = 0

    def _val(v: Any) -> Any:
        return None if pd.isna(v) else v

    rows_iter = df.reset_index(drop=True).itertuples(index=True, name="Row")

    try:
        with conn:
            cur = conn.cursor()

            for row in rows_iter:
                i = row.Index
                savepoint = f"sp_row_{i}"

                def _g(field, default=None):
                    v = getattr(row, field, default)
                    return default if (
                        v is None or (
                            isinstance(
                                v, float) and v != v)) else v

                try:
                    cur.execute(f"SAVEPOINT {savepoint}")

                    id_loc = _upsert_localisation(
                        cur,
                        str(_g("ville", "")),
                        str(_g("quartier", "")) or None,
                        str(_g("region_label", "Autre")),
                        bool(_g("is_grande_ville", False)),
                    )
                    id_car = _upsert_caracteristiques(
                        cur,
                        _safe_int(_g("nb_chambres")),
                        _safe_int(_g("nb_salles_bain")),
                        _safe_int(_g("etage")),
                    )
                    _scraped_at = getattr(row, "scraped_at", None)
                    _scraped_at_str = str(_scraped_at) if _scraped_at is not None else datetime.utcnow().isoformat()
                    id_tps = _upsert_temps(cur, _scraped_at_str)

                    cur.execute(
                        """
                        INSERT INTO bi_schema.fact_annonce
                            (id_localisation, id_caracteristiques, id_temps,
                             titre, prix, prix_type, surface_m2, prix_par_m2,
                             categorie_prix, lien)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (lien) DO UPDATE SET
                            id_localisation = EXCLUDED.id_localisation,
                            id_caracteristiques = EXCLUDED.id_caracteristiques,
                            id_temps = EXCLUDED.id_temps,
                            titre = EXCLUDED.titre,
                            prix = EXCLUDED.prix,
                            prix_type = EXCLUDED.prix_type,
                            surface_m2 = EXCLUDED.surface_m2,
                            prix_par_m2 = EXCLUDED.prix_par_m2,
                            categorie_prix = EXCLUDED.categorie_prix,
                            loaded_at = NOW()
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
                    logger.warning(
                        f"Row {i} skipped: rolled back cleanly: {e}")

            cur.close()

    finally:
        release_connection(conn)

    logger.info(
        f"=== BI Schema load finished: {count} inserted, {skipped} skipped ==="
    )

    for view_sql in _VIEWS:
        execute_query(view_sql)
    logger.info("Power BI helper views created successfully.")

    _validate(inserted_this_run=count)
    _save_gold_bi()


def _save_gold_bi() -> None:
    """Export dated BI snapshots to CSV and Parquet."""
    from datetime import timezone
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    date_pfx = datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
    part_dir = os.path.join(GOLD_BI_DIR, date_pfx)
    os.makedirs(part_dir, exist_ok=True)

    exports = [
        ("SELECT * FROM bi_schema.v_annonces_full;", f"annonces_full_{ts}"),
        ("SELECT * FROM bi_schema.v_prix_par_ville;", f"prix_par_ville_{ts}"),
        ("SELECT * FROM bi_schema.v_prix_par_quartier;", f"prix_par_quartier_{ts}"),
    ]

    conn = get_connection()
    exported_ok = 0
    try:
        for sql, stem in exports:
            try:
                df = pd.read_sql(sql, conn)
                if df.empty:
                    logger.warning(f"Gold BI: {stem}: query returned 0 rows.")
                    continue

                csv_path = os.path.join(part_dir, f"{stem}.csv")
                df.to_csv(csv_path, index=False, encoding="utf-8")
                logger.info(f"Gold BI CSV saved to {csv_path} ({len(df)} rows)")

                parquet_path = os.path.join(part_dir, f"{stem}.parquet")
                df.to_parquet(parquet_path, index=False, engine="pyarrow")
                logger.info("Gold BI Parquet saved to %s (%s rows)", parquet_path, len(df))

                exported_ok += 1
            except Exception:
                logger.exception("Gold BI export failed for %s", stem)
                raise
    finally:
        release_connection(conn)

    if exported_ok == 0:
        raise RuntimeError("Gold BI export produced no data")
    else:
        logger.info(
            f"Gold BI: {exported_ok}/{len(exports)} exports saved successfully.")
