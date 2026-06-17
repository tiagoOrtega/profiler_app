-- =============================================================================
-- migrate_date_key_to_date.sql
--
-- Migrates DATE_KEY from NUMBER(8) (YYYYMMDD surrogate) to native DATE
-- across the full lineage:
--
--   DIM_DATE            (730 rows   · PK holder)
--   FACT_SALES          (10 000 rows · FK holder)
--   FACT_SALES_ORGANIC  (12 000 000 rows · FK holder · CLUSTER BY)
--
-- Strategy
-- ─────────
--   DIM_DATE / FACT_SALES : add staging col → UPDATE → drop old → rename
--   FACT_SALES_ORGANIC    : CTAS + SWAP (avoids large in-place mutation)
--
-- Incremental / idempotent
-- ─────────────────────────
--   Every phase begins with an INFORMATION_SCHEMA guard.
--   If DATE_KEY is already DATE the phase prints a notice and exits.
--   The UPDATE backfills are WHERE col IS NULL, so a partial run resumes.
--   Run phases 0→5 in order; re-running any completed phase is safe.
--
-- Usage
-- ──────
--   1. Set <DATABASE> and <SCHEMA> in Phase 0.
--   2. Run Phase 1 (pre-flight). Fix any invalid_keys before continuing.
--   3. Run Phase 2 (DIM_DATE). Verify, then commit or roll back.
--   4. Run Phase 3 (FACT_SALES).
--   5. Run Phase 4 (FACT_SALES_ORGANIC — may take several minutes).
--   6. Run Phase 5 (post-migration validation).
-- =============================================================================


-- =============================================================================
-- PHASE 0 — Session context
-- =============================================================================

USE DATABASE <DATABASE>;   -- ← replace
USE SCHEMA   <SCHEMA>;     -- ← replace


-- =============================================================================
-- PHASE 1 — Pre-flight validation
--   Run these SELECT statements and inspect the results before proceeding.
--   DO NOT continue if any check fails.
-- =============================================================================

-- 1.1  Current DATE_KEY types across all three tables
SELECT
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE,
    IS_NULLABLE,
    ORDINAL_POSITION
FROM INFORMATION_SCHEMA.COLUMNS
WHERE COLUMN_NAME = 'DATE_KEY'
  AND TABLE_NAME  IN ('DIM_DATE', 'FACT_SALES', 'FACT_SALES_ORGANIC')
ORDER BY TABLE_NAME;
-- Expected before migration : DATA_TYPE = 'FIXED' (Snowflake's internal name for NUMBER)
-- Expected after  migration : DATA_TYPE = 'DATE'

-- 1.2  Validate all DATE_KEY values parse as valid dates (must be 0 for all)
SELECT
    'DIM_DATE' AS table_name,
    COUNT(*)   AS total_rows,
    SUM(IFF(TRY_TO_DATE(DATE_KEY::VARCHAR, 'YYYYMMDD') IS NULL, 1, 0)) AS invalid_keys
FROM DIM_DATE
UNION ALL
SELECT 'FACT_SALES',
    COUNT(*),
    SUM(IFF(TRY_TO_DATE(DATE_KEY::VARCHAR, 'YYYYMMDD') IS NULL, 1, 0))
FROM FACT_SALES
UNION ALL
SELECT 'FACT_SALES_ORGANIC',
    COUNT(*),
    SUM(IFF(TRY_TO_DATE(DATE_KEY::VARCHAR, 'YYYYMMDD') IS NULL, 1, 0))
FROM FACT_SALES_ORGANIC;
-- ⚠  STOP if invalid_keys > 0 in any row.  Fix bad data before continuing.

-- 1.3  Row counts — save these numbers for the post-migration diff in Phase 5
SELECT 'DIM_DATE'           AS table_name, COUNT(*) AS row_count FROM DIM_DATE
UNION ALL
SELECT 'FACT_SALES',                        COUNT(*) FROM FACT_SALES
UNION ALL
SELECT 'FACT_SALES_ORGANIC',                COUNT(*) FROM FACT_SALES_ORGANIC;

-- 1.4  Orphan check — FK integrity before the migration
SELECT
    'DIM_DATE → FACT_SALES'          AS check_name,
    COUNT(*)                          AS orphan_rows
FROM FACT_SALES f
WHERE NOT EXISTS (SELECT 1 FROM DIM_DATE d WHERE d.DATE_KEY = f.DATE_KEY)
UNION ALL
SELECT
    'DIM_DATE → FACT_SALES_ORGANIC',
    COUNT(*)
FROM FACT_SALES_ORGANIC f
WHERE NOT EXISTS (SELECT 1 FROM DIM_DATE d WHERE d.DATE_KEY = f.DATE_KEY);
-- ⚠  STOP if orphan_rows > 0.  Investigate before continuing.


-- =============================================================================
-- PHASE 2 — Migrate DIM_DATE  (730 rows, PK holder)
-- =============================================================================

-- Guard: skip if DATE_KEY is already DATE
DECLARE
    v_type STRING;
BEGIN
    SELECT DATA_TYPE INTO :v_type
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = CURRENT_SCHEMA()
      AND TABLE_NAME   = 'DIM_DATE'
      AND COLUMN_NAME  = 'DATE_KEY';

    IF (v_type = 'DATE') THEN
        RETURN '[Phase 2] DIM_DATE.DATE_KEY is already DATE — skipping.';
    END IF;
END;

-- 2a.  Add staging column (ADD COLUMN IF NOT EXISTS is idempotent)
ALTER TABLE DIM_DATE ADD COLUMN IF NOT EXISTS DATE_KEY_NEW DATE;

-- 2b.  Backfill — only rows not yet converted (safe to re-run)
--      DIM_DATE.FULL_DATE already holds the same value; we use it as the source
--      to avoid any number-to-string formatting edge cases.
UPDATE DIM_DATE
SET    DATE_KEY_NEW = FULL_DATE
WHERE  DATE_KEY_NEW IS NULL;

-- 2c.  Verify backfill — must show unset_rows = 0 and mismatches = 0
SELECT
    COUNT(*)                                        AS total_rows,
    SUM(IFF(DATE_KEY_NEW IS NULL, 1, 0))            AS unset_rows,
    SUM(IFF(DATE_KEY_NEW <> FULL_DATE, 1, 0))       AS mismatches_with_full_date
FROM DIM_DATE;
-- ⚠  STOP if unset_rows > 0 or mismatches > 0.

-- 2d.  Remove PK (required before dropping the column that backs it)
ALTER TABLE DIM_DATE DROP PRIMARY KEY;

-- 2e.  Drop original NUMBER(8) column
ALTER TABLE DIM_DATE DROP COLUMN DATE_KEY;

-- 2f.  Rename staging column to the canonical name
ALTER TABLE DIM_DATE RENAME COLUMN DATE_KEY_NEW TO DATE_KEY;

-- 2g.  Enforce NOT NULL and recreate PK
ALTER TABLE DIM_DATE ALTER COLUMN DATE_KEY SET NOT NULL;
ALTER TABLE DIM_DATE ADD CONSTRAINT PK_DIM_DATE PRIMARY KEY (DATE_KEY);

-- 2h.  Spot-check: DATE_KEY should now equal FULL_DATE for every row
SELECT DATE_KEY, FULL_DATE, IFF(DATE_KEY = FULL_DATE, 'OK', 'MISMATCH') AS check_val
FROM   DIM_DATE
ORDER  BY DATE_KEY
LIMIT  10;


-- =============================================================================
-- PHASE 3 — Migrate FACT_SALES  (10 000 rows, FK holder)
-- =============================================================================

-- Guard
DECLARE
    v_type STRING;
BEGIN
    SELECT DATA_TYPE INTO :v_type
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = CURRENT_SCHEMA()
      AND TABLE_NAME   = 'FACT_SALES'
      AND COLUMN_NAME  = 'DATE_KEY';

    IF (v_type = 'DATE') THEN
        RETURN '[Phase 3] FACT_SALES.DATE_KEY is already DATE — skipping.';
    END IF;
END;

-- 3a.  Add staging column
ALTER TABLE FACT_SALES ADD COLUMN IF NOT EXISTS DATE_KEY_NEW DATE;

-- 3b.  Backfill
UPDATE FACT_SALES
SET    DATE_KEY_NEW = TO_DATE(DATE_KEY::VARCHAR, 'YYYYMMDD')
WHERE  DATE_KEY_NEW IS NULL;

-- 3c.  Verify
SELECT
    COUNT(*)                                  AS total_rows,
    SUM(IFF(DATE_KEY_NEW IS NULL, 1, 0))      AS unset_rows
FROM FACT_SALES;
-- ⚠  STOP if unset_rows > 0.

-- 3d.  Drop original column (no PK on FACT_SALES, so no constraint to drop first)
ALTER TABLE FACT_SALES DROP COLUMN DATE_KEY;

-- 3e.  Rename
ALTER TABLE FACT_SALES RENAME COLUMN DATE_KEY_NEW TO DATE_KEY;

-- 3f.  NOT NULL
ALTER TABLE FACT_SALES ALTER COLUMN DATE_KEY SET NOT NULL;

-- 3g.  Spot-check: join with DIM_DATE using the new DATE type key
SELECT
    f.SALE_KEY,
    f.DATE_KEY,
    d.FULL_DATE,
    IFF(f.DATE_KEY = d.DATE_KEY, 'OK', 'MISMATCH') AS join_check
FROM FACT_SALES   f
JOIN DIM_DATE     d ON d.DATE_KEY = f.DATE_KEY
LIMIT 5;


-- =============================================================================
-- PHASE 4 — Migrate FACT_SALES_ORGANIC  (12 000 000 rows, FK + CLUSTER BY)
--
-- Uses CTAS + SWAP to avoid an in-place UPDATE on 12M rows:
--   1. CREATE TABLE … AS SELECT converts DATE_KEY in one scan.
--   2. Counts are compared before the swap.
--   3. ALTER TABLE … SWAP WITH is atomic (no window of missing data).
--   4. Old table (now renamed to …_MIGRATED) is dropped.
--   5. CLUSTER BY is re-declared on the new table with the DATE type key.
--
-- Expected runtime: 2–5 minutes depending on warehouse size.
-- =============================================================================

-- Guard
DECLARE
    v_type STRING;
BEGIN
    SELECT DATA_TYPE INTO :v_type
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = CURRENT_SCHEMA()
      AND TABLE_NAME   = 'FACT_SALES_ORGANIC'
      AND COLUMN_NAME  = 'DATE_KEY';

    IF (v_type = 'DATE') THEN
        RETURN '[Phase 4] FACT_SALES_ORGANIC.DATE_KEY is already DATE — skipping.';
    END IF;
END;

-- 4a.  Build the migrated copy with DATE_KEY as DATE
--      Column order is preserved; DATE_KEY moves to position 2 as before.
CREATE OR REPLACE TABLE FACT_SALES_ORGANIC_MIGRATED
COMMENT = '12M organic sales transactions with realistic seasonal, channel and product-affinity distributions.'
CLUSTER BY (DATE_KEY, CUSTOMER_KEY)
AS
SELECT
    SALE_KEY,
    TO_DATE(DATE_KEY::VARCHAR, 'YYYYMMDD')  AS DATE_KEY,
    CUSTOMER_KEY,
    PRODUCT_KEY,
    STORE_KEY,
    EMPLOYEE_KEY,
    PROMOTION_KEY,
    CHANNEL_KEY,
    TRANSACTION_ID,
    PAYMENT_METHOD,
    QUANTITY,
    UNIT_PRICE,
    UNIT_COST,
    DISCOUNT_AMOUNT,
    GROSS_AMOUNT,
    NET_AMOUNT,
    TAX_AMOUNT,
    MARGIN_AMOUNT,
    IS_WEEKEND,
    IS_HOLIDAY,
    SEASON
FROM FACT_SALES_ORGANIC;

-- 4b.  Count parity check — must be 0
SELECT
    (SELECT COUNT(*) FROM FACT_SALES_ORGANIC)          AS src_count,
    (SELECT COUNT(*) FROM FACT_SALES_ORGANIC_MIGRATED) AS dst_count,
    (SELECT COUNT(*) FROM FACT_SALES_ORGANIC)
    - (SELECT COUNT(*) FROM FACT_SALES_ORGANIC_MIGRATED) AS diff;
-- ⚠  STOP if diff <> 0.  The CTAS did not complete cleanly.

-- 4c.  Verify DATA_TYPE in the new copy
SELECT DATA_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME  = 'FACT_SALES_ORGANIC_MIGRATED'
  AND COLUMN_NAME = 'DATE_KEY';
-- Expected: DATE

-- 4d.  Spot-check date range consistency
SELECT
    MIN(DATE_KEY) AS min_date,
    MAX(DATE_KEY) AS max_date,
    COUNT(DISTINCT DATE_KEY) AS distinct_dates
FROM FACT_SALES_ORGANIC_MIGRATED;
-- Expected: 2023-01-01 → 2024-12-31, 730 distinct dates

-- 4e.  Atomic swap
ALTER TABLE FACT_SALES_ORGANIC SWAP WITH FACT_SALES_ORGANIC_MIGRATED;
-- After swap: FACT_SALES_ORGANIC holds the migrated DATE data.
--             FACT_SALES_ORGANIC_MIGRATED holds the old NUMBER(8) data.

-- 4f.  Drop the old copy (now carrying the NUMBER(8) data under the _MIGRATED name)
DROP TABLE FACT_SALES_ORGANIC_MIGRATED;

-- 4g.  Declare the cluster key with the DATE type (updates Snowflake's metadata)
ALTER TABLE FACT_SALES_ORGANIC CLUSTER BY (DATE_KEY, CUSTOMER_KEY);

-- 4h.  Kick off automatic re-clustering in the background
ALTER TABLE FACT_SALES_ORGANIC RESUME RECLUSTER;

-- 4i.  Spot-check join with DIM_DATE
SELECT
    f.SALE_KEY,
    f.DATE_KEY,
    d.FULL_DATE,
    IFF(f.DATE_KEY = d.DATE_KEY, 'OK', 'MISMATCH') AS join_check
FROM FACT_SALES_ORGANIC  f
JOIN DIM_DATE            d ON d.DATE_KEY = f.DATE_KEY
LIMIT 5;


-- =============================================================================
-- PHASE 5 — Post-migration validation
-- =============================================================================

-- 5.1  Final schema: all three DATE_KEY columns must be DATE
SELECT
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE,
    IS_NULLABLE,
    ORDINAL_POSITION
FROM INFORMATION_SCHEMA.COLUMNS
WHERE COLUMN_NAME = 'DATE_KEY'
  AND TABLE_NAME  IN ('DIM_DATE', 'FACT_SALES', 'FACT_SALES_ORGANIC')
ORDER BY TABLE_NAME;
-- Expected: DATA_TYPE = 'DATE' for all three rows.

-- 5.2  PK still present on DIM_DATE
SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE
FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
WHERE TABLE_NAME      = 'DIM_DATE'
  AND CONSTRAINT_TYPE = 'PRIMARY KEY'
  AND TABLE_SCHEMA    = CURRENT_SCHEMA();

-- 5.3  Row counts must match Phase 1 baselines exactly
SELECT 'DIM_DATE'           AS table_name, COUNT(*) AS row_count FROM DIM_DATE
UNION ALL
SELECT 'FACT_SALES',                        COUNT(*) FROM FACT_SALES
UNION ALL
SELECT 'FACT_SALES_ORGANIC',                COUNT(*) FROM FACT_SALES_ORGANIC;

-- 5.4  Referential integrity (orphan_rows must be 0)
SELECT
    'DIM_DATE → FACT_SALES'          AS check_name,
    COUNT(*)                          AS orphan_rows
FROM FACT_SALES f
WHERE NOT EXISTS (SELECT 1 FROM DIM_DATE d WHERE d.DATE_KEY = f.DATE_KEY)
UNION ALL
SELECT
    'DIM_DATE → FACT_SALES_ORGANIC',
    COUNT(*)
FROM FACT_SALES_ORGANIC f
WHERE NOT EXISTS (SELECT 1 FROM DIM_DATE d WHERE d.DATE_KEY = f.DATE_KEY);

-- 5.5  Date range sanity across fact tables
SELECT
    'FACT_SALES'           AS table_name,
    MIN(DATE_KEY)          AS min_date,
    MAX(DATE_KEY)          AS max_date,
    COUNT(DISTINCT DATE_KEY) AS distinct_dates
FROM FACT_SALES
UNION ALL
SELECT
    'FACT_SALES_ORGANIC',
    MIN(DATE_KEY),
    MAX(DATE_KEY),
    COUNT(DISTINCT DATE_KEY)
FROM FACT_SALES_ORGANIC;

-- 5.6  Representative join query — same logic as application queries
SELECT
    d.YEAR,
    d.MONTH_NUM,
    d.MONTH_NAME,
    SUM(f.NET_AMOUNT)  AS net_revenue,
    COUNT(*)           AS transactions
FROM FACT_SALES   f
JOIN DIM_DATE     d ON d.DATE_KEY = f.DATE_KEY
GROUP BY d.YEAR, d.MONTH_NUM, d.MONTH_NAME
ORDER BY d.YEAR, d.MONTH_NUM
LIMIT 12;

-- 5.7  Same query on the large table (limit to one month for speed)
SELECT
    d.YEAR,
    d.MONTH_NUM,
    COUNT(*)                 AS transactions,
    SUM(f.NET_AMOUNT)        AS net_revenue
FROM FACT_SALES_ORGANIC  f
JOIN DIM_DATE            d ON d.DATE_KEY = f.DATE_KEY
WHERE d.YEAR = 2023 AND d.MONTH_NUM = 1
GROUP BY d.YEAR, d.MONTH_NUM;

-- 5.8  Clustering depth check for the large table
SELECT SYSTEM$CLUSTERING_INFORMATION('FACT_SALES_ORGANIC', '(DATE_KEY, CUSTOMER_KEY)');


-- =============================================================================
-- ROLLBACK SCRIPT
-- Run this only if you need to revert AFTER Phase 2 (DIM_DATE) has completed
-- but BEFORE the application goes live with the new type.
-- =============================================================================

/*
-- RB-1: Revert DIM_DATE
ALTER TABLE DIM_DATE DROP PRIMARY KEY;
ALTER TABLE DIM_DATE ADD COLUMN IF NOT EXISTS DATE_KEY_OLD NUMBER(8);
UPDATE DIM_DATE SET DATE_KEY_OLD = TO_NUMBER(TO_CHAR(DATE_KEY, 'YYYYMMDD')) WHERE DATE_KEY_OLD IS NULL;
ALTER TABLE DIM_DATE ALTER COLUMN DATE_KEY_OLD SET NOT NULL;
ALTER TABLE DIM_DATE DROP COLUMN DATE_KEY;
ALTER TABLE DIM_DATE RENAME COLUMN DATE_KEY_OLD TO DATE_KEY;
ALTER TABLE DIM_DATE ADD PRIMARY KEY (DATE_KEY);

-- RB-2: Revert FACT_SALES
ALTER TABLE FACT_SALES ADD COLUMN IF NOT EXISTS DATE_KEY_OLD NUMBER(8);
UPDATE FACT_SALES SET DATE_KEY_OLD = TO_NUMBER(TO_CHAR(DATE_KEY, 'YYYYMMDD')) WHERE DATE_KEY_OLD IS NULL;
ALTER TABLE FACT_SALES ALTER COLUMN DATE_KEY_OLD SET NOT NULL;
ALTER TABLE FACT_SALES DROP COLUMN DATE_KEY;
ALTER TABLE FACT_SALES RENAME COLUMN DATE_KEY_OLD TO DATE_KEY;

-- RB-3: Revert FACT_SALES_ORGANIC (CTAS + SWAP — same pattern as Phase 4)
CREATE OR REPLACE TABLE FACT_SALES_ORGANIC_ROLLBACK
CLUSTER BY (DATE_KEY, CUSTOMER_KEY)
AS
SELECT
    SALE_KEY,
    TO_NUMBER(TO_CHAR(DATE_KEY, 'YYYYMMDD'))  AS DATE_KEY,
    CUSTOMER_KEY, PRODUCT_KEY, STORE_KEY, EMPLOYEE_KEY,
    PROMOTION_KEY, CHANNEL_KEY, TRANSACTION_ID, PAYMENT_METHOD,
    QUANTITY, UNIT_PRICE, UNIT_COST, DISCOUNT_AMOUNT,
    GROSS_AMOUNT, NET_AMOUNT, TAX_AMOUNT, MARGIN_AMOUNT,
    IS_WEEKEND, IS_HOLIDAY, SEASON
FROM FACT_SALES_ORGANIC;
ALTER TABLE FACT_SALES_ORGANIC SWAP WITH FACT_SALES_ORGANIC_ROLLBACK;
DROP TABLE FACT_SALES_ORGANIC_ROLLBACK;
*/
