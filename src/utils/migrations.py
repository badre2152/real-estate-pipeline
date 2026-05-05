"""
migrations.py — Centralised DDL migration registry.

FIX #14: Instead of _DDL_MIGRATIONS scattered across bi_schema.py,
ml_schema.py, and clean_data.py, all migrations are defined here and
applied in order via run_all_migrations().

Each migration has a unique name. Applied migrations are tracked in the
schema_migrations table so each one only ever runs once — even if the
pipeline is restarted.

FIX #SCHEMA: Added bootstrap migrations (boot_*) that create all schemas
and base tables before any ALTER TABLE migration runs. Previously,
clean_001–clean_007 would fail on a fresh DB because clean.annonces
was only created later inside _load_to_db() (called during the CLEAN
pipeline step — well after MIGRATIONS runs). The boot_* entries are
idempotent (IF NOT EXISTS) and safe to run repeatedly.
"""

from src.utils.db import execute_query, fetch_all
from src.utils.logger import get_logger

logger = get_logger("migrations")

# ── Migration tracking table ─────────────────────────────────────────────────

_DDL_TRACKING = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT NOW()
);
"""

# ── Migration registry ───────────────────────────────────────────────────────
# Order matters: each entry is (name, sql).
# ADD new migrations at the END — never reorder or delete existing entries.

MIGRATIONS: list[tuple[str, str]] = [
    # ── bootstrap: create schemas + base tables ────────────────────────────
    # These run FIRST so every subsequent ALTER TABLE has a table to target.
    # All statements are fully idempotent (IF NOT EXISTS).
    ("boot_001_clean_schema",
     "CREATE SCHEMA IF NOT EXISTS clean;"),

    ("boot_002_clean_annonces",
     """CREATE TABLE IF NOT EXISTS clean.annonces (
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
         annee_construction  INTEGER,
         lien                TEXT UNIQUE,
         scraped_at          TIMESTAMP,
         prix_par_m2         NUMERIC,
         age_bien            INTEGER,
         categorie_prix      TEXT,
         region_label        TEXT,
         is_grande_ville     BOOLEAN,
         loaded_at           TIMESTAMP DEFAULT NOW()
     );"""),

    ("boot_003_bi_schema",
     "CREATE SCHEMA IF NOT EXISTS bi_schema;"),

    ("boot_004_bi_dim_localisation",
     """CREATE TABLE IF NOT EXISTS bi_schema.dim_localisation (
         id_localisation SERIAL PRIMARY KEY,
         ville           TEXT NOT NULL,
         quartier        TEXT NOT NULL DEFAULT '',
         region_label    TEXT NOT NULL DEFAULT 'Autre',
         is_grande_ville BOOLEAN NOT NULL DEFAULT FALSE,
         UNIQUE (ville, quartier)
     );"""),

    # FIX #7: boot_005 was two DDL statements in one string (CREATE TABLE + CREATE UNIQUE INDEX).
    # Some drivers execute them as a single statement and fail. Split into two idempotent migrations.
    ("boot_005_bi_dim_caracteristiques",
     """CREATE TABLE IF NOT EXISTS bi_schema.dim_caracteristiques (
         id_caracteristiques SERIAL PRIMARY KEY,
         nb_chambres         BIGINT,
         nb_salles_bain      BIGINT,
         etage               TEXT NOT NULL DEFAULT '',
         annee_construction  BIGINT,
         age_bien            BIGINT
     );"""),

    ("boot_005b_bi_dim_caracteristiques_uq_idx",
     """CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_caracteristiques
     ON bi_schema.dim_caracteristiques (
         COALESCE(nb_chambres, -1),
         COALESCE(nb_salles_bain, -1),
         etage,
         COALESCE(annee_construction, -1)
     );"""),

    ("boot_006_bi_dim_temps",
     """CREATE TABLE IF NOT EXISTS bi_schema.dim_temps (
         id_temps     SERIAL PRIMARY KEY,
         date_jour    DATE NOT NULL UNIQUE,
         annee        INTEGER,
         trimestre    INTEGER,
         mois         INTEGER,
         jour         INTEGER,
         jour_semaine INTEGER
     );"""),

    ("boot_007_bi_fact_annonce",
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
     );"""),

    ("boot_008_ml_schema",
     "CREATE SCHEMA IF NOT EXISTS ml_schema;"),

    ("boot_009_ml_feature_store",
     """CREATE TABLE IF NOT EXISTS ml_schema.feature_store (
         id                  SERIAL PRIMARY KEY,
         prix                NUMERIC,
         ville               TEXT,
         quartier            TEXT,
         surface_m2          NUMERIC,
         nb_chambres         INTEGER,
         nb_salles_bain      INTEGER,
         etage               TEXT,
         annee_construction  INTEGER,
         prix_par_m2         NUMERIC,
         age_bien            INTEGER,
         categorie_prix      TEXT,
         prix_type           TEXT DEFAULT 'mensuel',
         titre               TEXT,
         lien                TEXT UNIQUE,
         scraped_at          TIMESTAMP,
         loaded_at           TIMESTAMP DEFAULT NOW()
     );"""),

    ("boot_010_staging_schema",
     "CREATE SCHEMA IF NOT EXISTS staging;"),

    # FIX: Create staging.raw_annonces early (in migrations) so that
    # staging_001_unique_lien can safely add the UNIQUE constraint on lien.
    # Previously the table was only created inside run_staging() — AFTER
    # migrations ran — so the constraint was never applied and ON CONFLICT
    # (lien) always failed with "no unique constraint matching specification".
    ("boot_011_staging_raw_annonces",
     """CREATE TABLE IF NOT EXISTS staging.raw_annonces (
         id                  SERIAL PRIMARY KEY,
         run_id              TEXT NOT NULL DEFAULT 'legacy',
         titre               TEXT,
         prix                TEXT,
         prix_type           TEXT DEFAULT 'mensuel',
         ville               TEXT,
         quartier            TEXT,
         surface             TEXT,
         nb_chambres         TEXT,
         nb_salles_bain      TEXT,
         etage               TEXT,
         annee_construction  TEXT,
         lien                TEXT,
         scraped_at          TEXT,
         loaded_at           TIMESTAMP DEFAULT NOW()
     );"""),

    # ── clean schema ───────────────────────────────────────────────────────
    ("clean_001_add_region_label",
     "ALTER TABLE clean.annonces ADD COLUMN IF NOT EXISTS region_label TEXT;"),
    ("clean_002_unique_lien",
     """DO $$ BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM pg_constraint
             WHERE conname = 'annonces_lien_key'
               AND conrelid = 'clean.annonces'::regclass
         ) THEN
             ALTER TABLE clean.annonces ADD CONSTRAINT annonces_lien_key UNIQUE (lien);
         END IF;
     END $$;"""),
    ("clean_003_add_is_grande_ville",
     "ALTER TABLE clean.annonces ADD COLUMN IF NOT EXISTS is_grande_ville BOOLEAN;"),
    ("clean_004_add_prix_par_m2",
     "ALTER TABLE clean.annonces ADD COLUMN IF NOT EXISTS prix_par_m2 NUMERIC;"),
    ("clean_005_add_age_bien",
     "ALTER TABLE clean.annonces ADD COLUMN IF NOT EXISTS age_bien INTEGER;"),
    ("clean_006_add_categorie_prix",
     "ALTER TABLE clean.annonces ADD COLUMN IF NOT EXISTS categorie_prix TEXT;"),
    ("clean_007_add_prix_type",
     "ALTER TABLE clean.annonces ADD COLUMN IF NOT EXISTS prix_type TEXT DEFAULT 'mensuel';"),

    # ── staging schema ─────────────────────────────────────────────────────
    # FIX: Table is now guaranteed to exist (boot_011_staging_raw_annonces runs first),
    # so we can safely add the UNIQUE constraint here without the IF EXISTS table check.
    ("staging_001_unique_lien",
     """DO $$ BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM pg_constraint
             WHERE conname = 'raw_annonces_lien_key'
               AND conrelid = to_regclass('staging.raw_annonces')
         ) THEN
             ALTER TABLE staging.raw_annonces ADD CONSTRAINT raw_annonces_lien_key UNIQUE (lien);
         END IF;
     END $$;"""),

    # ── bi_schema ──────────────────────────────────────────────────────────
    ("bi_001_add_region_label",
     "ALTER TABLE bi_schema.dim_localisation ADD COLUMN IF NOT EXISTS region_label TEXT NOT NULL DEFAULT 'Autre';"),
    ("bi_002_add_is_grande_ville",
     "ALTER TABLE bi_schema.dim_localisation ADD COLUMN IF NOT EXISTS is_grande_ville BOOLEAN NOT NULL DEFAULT FALSE;"),
    ("bi_003_add_lien",
     "ALTER TABLE bi_schema.fact_annonce ADD COLUMN IF NOT EXISTS lien TEXT;"),
    ("bi_004_add_prix_type",
     "ALTER TABLE bi_schema.fact_annonce ADD COLUMN IF NOT EXISTS prix_type TEXT DEFAULT 'mensuel';"),
    ("bi_005_unique_lien",
     """DO $$ BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM pg_constraint
             WHERE conname = 'fact_annonce_lien_key'
               AND conrelid = 'bi_schema.fact_annonce'::regclass
         ) THEN
             ALTER TABLE bi_schema.fact_annonce ADD CONSTRAINT fact_annonce_lien_key UNIQUE (lien);
         END IF;
     END $$;"""),

    # ── ml_schema ──────────────────────────────────────────────────────────
    ("ml_001_add_prix_type",
     "ALTER TABLE ml_schema.feature_store ADD COLUMN IF NOT EXISTS prix_type TEXT DEFAULT 'mensuel';"),
]


# ── Runner ───────────────────────────────────────────────────────────────────

def run_all_migrations() -> None:
    """Apply all pending migrations in order. Each migration runs at most once."""
    execute_query(_DDL_TRACKING)

    applied = {row[0] for row in fetch_all("SELECT name FROM public.schema_migrations;")}
    pending = [(name, sql) for name, sql in MIGRATIONS if name not in applied]

    if not pending:
        logger.info("Migrations: all up-to-date, nothing to run.")
        return

    logger.info(f"Migrations: {len(pending)} pending out of {len(MIGRATIONS)} total.")
    for name, sql in pending:
        try:
            execute_query(sql)
            execute_query(
                "INSERT INTO public.schema_migrations (name) VALUES (%s) ON CONFLICT DO NOTHING;",
                (name,),
            )
            logger.info(f"  ✅ Migration applied: {name}")
        except Exception as exc:
            logger.error(f"  ❌ Migration failed ({name}): {exc}")
            raise