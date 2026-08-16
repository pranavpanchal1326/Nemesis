-- Runs once, on first initialisation of an empty data volume.
--
-- Extensions are created here rather than in an Alembic migration because they
-- require superuser and are a property of the *database*, not the schema. The
-- /ready probe asserts both are present, so a mis-provisioned volume fails at
-- boot rather than at the first dedup query.

CREATE EXTENSION IF NOT EXISTS postgis;      -- §14.1 Stage 1: ST_DWithin geo filter
CREATE EXTENSION IF NOT EXISTS vector;       -- §14.1 Stage 2: embedding cosine similarity
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid() for entity ids
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy contractor-name matching (§17.1)

-- Fail loudly and immediately if the image was built without pgvector, rather
-- than surfacing as a confusing "type vector does not exist" mid-migration.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RAISE EXCEPTION 'pgvector missing: rebuild infra/postgres image';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
        RAISE EXCEPTION 'postgis missing: rebuild infra/postgres image';
    END IF;
END
$$;
