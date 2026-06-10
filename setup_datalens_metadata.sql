-- =============================================================================
-- DataLens — Metadata Schema Setup
-- Run this once in your Snowflake account before deploying the Streamlit app.
-- Creates the DATALENS database and all required metadata tables.
-- =============================================================================

-- ── Database & Schema ─────────────────────────────────────────────────────────

CREATE DATABASE IF NOT EXISTS DATALENS;
CREATE SCHEMA IF NOT EXISTS DATALENS.METADATA;

-- ── Profile Results ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS DATALENS.METADATA.PROFILE_RESULTS (
    PROFILE_KEY   VARCHAR(500)  NOT NULL,   -- DB__SCHEMA__TABLE (uppercase)
    DATABASE_NAME VARCHAR(255),
    SCHEMA_NAME   VARCHAR(255),
    TABLE_NAME    VARCHAR(255),
    PROFILE_JSON  VARCHAR,                  -- JSON-serialized TableProfile dict
    CREATED_AT    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_PROFILE_RESULTS PRIMARY KEY (PROFILE_KEY)
);

-- ── Relationship Results ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS DATALENS.METADATA.RELATIONSHIP_RESULTS (
    PROFILE_KEY VARCHAR(500) NOT NULL,
    RESULT_JSON VARCHAR,
    CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_REL_RESULTS PRIMARY KEY (PROFILE_KEY)
);

-- ── Correlation Results ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS DATALENS.METADATA.CORRELATION_RESULTS (
    PROFILE_KEY VARCHAR(500) NOT NULL,
    RESULT_JSON VARCHAR,
    CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CORR_RESULTS PRIMARY KEY (PROFILE_KEY)
);

-- ── Clustering Results ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS DATALENS.METADATA.CLUSTERING_RESULTS (
    PROFILE_KEY VARCHAR(500) NOT NULL,
    RESULT_JSON VARCHAR,
    CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CLUST_RESULTS PRIMARY KEY (PROFILE_KEY)
);

-- ── Column Colors ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS DATALENS.METADATA.COLUMN_COLORS (
    PROFILE_KEY VARCHAR(500) NOT NULL,
    COLUMN_NAME VARCHAR(255) NOT NULL,
    COLOR       VARCHAR(50),
    CONSTRAINT PK_COLUMN_COLORS PRIMARY KEY (PROFILE_KEY, COLUMN_NAME)
);

-- ── App Settings ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS DATALENS.METADATA.APP_SETTINGS (
    SETTING_KEY   VARCHAR(255) NOT NULL,   -- e.g. ai_provider, cortex_model, ollama_url
    SETTING_VALUE VARCHAR,
    UPDATED_AT    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_APP_SETTINGS PRIMARY KEY (SETTING_KEY)
);

-- ── Verification ─────────────────────────────────────────────────────────────

SELECT
    TABLE_NAME,
    TABLE_TYPE
FROM DATALENS.INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'METADATA'
ORDER BY TABLE_NAME;
