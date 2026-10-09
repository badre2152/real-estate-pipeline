"""Ordered, transactional PostgreSQL schema migrations."""

from src.utils.logger import get_logger

logger = get_logger("migrations")


_DDL_TRACKING = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT NOW()
);
"""


MIGRATIONS: list[tuple[str, str]] = [
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
         lien                TEXT UNIQUE,
         scraped_at          TIMESTAMP,
         prix_par_m2         NUMERIC,
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
         quartier_known  BOOLEAN NOT NULL DEFAULT FALSE,
         region_label    TEXT NOT NULL DEFAULT 'Autre',
         is_grande_ville BOOLEAN NOT NULL DEFAULT FALSE,
         UNIQUE (ville, quartier)
     );"""),

    ("boot_005_bi_dim_caracteristiques",
     """CREATE TABLE IF NOT EXISTS bi_schema.dim_caracteristiques (
         id_caracteristiques SERIAL PRIMARY KEY,
         nb_chambres         BIGINT,
         nb_salles_bain      BIGINT,
         etage               TEXT NOT NULL DEFAULT ''
     );"""),

    ("boot_005b_bi_dim_caracteristiques_uq_idx",
     """CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_caracteristiques
     ON bi_schema.dim_caracteristiques (
         COALESCE(nb_chambres, -1),
         COALESCE(nb_salles_bain, -1),
         etage
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
         prix_par_m2         NUMERIC,
         categorie_prix      TEXT,
         prix_type           TEXT DEFAULT 'mensuel',
         titre               TEXT,
         lien                TEXT UNIQUE,
         scraped_at          TIMESTAMP,
         loaded_at           TIMESTAMP DEFAULT NOW()
     );"""),

    ("boot_010_staging_schema",
     "CREATE SCHEMA IF NOT EXISTS staging;"),

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

    ("ml_001_add_prix_type",
     "ALTER TABLE ml_schema.feature_store ADD COLUMN IF NOT EXISTS prix_type TEXT DEFAULT 'mensuel';"),

    ("bi_006_add_quartier_known",
     "ALTER TABLE bi_schema.dim_localisation ADD COLUMN IF NOT EXISTS quartier_known BOOLEAN NOT NULL DEFAULT FALSE;"),

    ("clean_008_drop_annee_construction",
     "ALTER TABLE clean.annonces DROP COLUMN IF EXISTS annee_construction;"),
    ("clean_009_drop_age_bien",
     "ALTER TABLE clean.annonces DROP COLUMN IF EXISTS age_bien;"),
    ("bi_007_drop_annee_construction",
     "ALTER TABLE bi_schema.dim_caracteristiques DROP COLUMN IF EXISTS annee_construction;"),
    ("bi_008_drop_age_bien",
     "ALTER TABLE bi_schema.dim_caracteristiques DROP COLUMN IF EXISTS age_bien;"),
    ("ml_002_drop_annee_construction",
     "ALTER TABLE ml_schema.feature_store DROP COLUMN IF EXISTS annee_construction;"),
    ("ml_003_drop_age_bien",
     "ALTER TABLE ml_schema.feature_store DROP COLUMN IF EXISTS age_bien;"),
    ("staging_002_drop_annee_construction",
     "ALTER TABLE staging.raw_annonces DROP COLUMN IF EXISTS annee_construction;"),
]



def run_all_migrations() -> None:
    """Apply pending schema changes and record each change atomically."""
    from src.utils.db import get_connection, release_connection

    connection = get_connection()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(_DDL_TRACKING)
                cursor.execute("SELECT name FROM public.schema_migrations")
                applied = {row[0] for row in cursor.fetchall()}
                pending = [
                    (name, sql) for name, sql in MIGRATIONS
                    if name not in applied
                ]
                for name, sql in pending:
                    cursor.execute(sql)
                    cursor.execute(
                        "INSERT INTO public.schema_migrations (name) "
                        "VALUES (%s) ON CONFLICT DO NOTHING",
                        (name,),
                    )
                    logger.info("Migration applied: %s", name)
                logger.info("Migrations completed: %s applied", len(pending))
    finally:
        release_connection(connection)
