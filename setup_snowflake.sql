-- =============================================================================
-- Snowflake Sample Data Warehouse — Retail Sales Star Schema
-- =============================================================================
--
--  Schema:
--
--    DIM_DATE  DIM_CUSTOMER  DIM_PROMOTION
--        |          |              |
--        └──────────┴──────────────┘
--                   |
--               FACT_SALES
--                   |
--        ┌──────────┼──────────┐
--    DIM_PRODUCT  DIM_STORE  DIM_EMPLOYEE
--
--  Table           Rows
--  --------------- ------
--  DIM_DATE           730   (2023-01-01 → 2024-12-31)
--  DIM_CUSTOMER       500
--  DIM_PRODUCT        200   (5 categories)
--  DIM_STORE           50   (10 US cities)
--  DIM_EMPLOYEE       100
--  DIM_PROMOTION       30
--  FACT_SALES      10 000
--
-- =============================================================================
-- ► Edit the warehouse name on the next line before running
-- =============================================================================
USE WAREHOUSE PROFILER_APP;

CREATE DATABASE IF NOT EXISTS SAMPLE_DW;
CREATE SCHEMA  IF NOT EXISTS SAMPLE_DW.RETAIL;
USE DATABASE SAMPLE_DW;
USE SCHEMA   RETAIL;

-- =============================================================================
-- 1. DIM_DATE  — 730 rows (2023-01-01 → 2024-12-31)
-- =============================================================================
CREATE OR REPLACE TABLE DIM_DATE (
    DATE_KEY      NUMBER(8)   NOT NULL  PRIMARY KEY,
    FULL_DATE     DATE        NOT NULL,
    DAY_OF_WEEK   NUMBER(1)   NOT NULL,   -- 0=Sun … 6=Sat
    DAY_NAME      VARCHAR(9)  NOT NULL,
    DAY_OF_MONTH  NUMBER(2)   NOT NULL,
    DAY_OF_YEAR   NUMBER(3)   NOT NULL,
    WEEK_OF_YEAR  NUMBER(2)   NOT NULL,
    MONTH_NUM     NUMBER(2)   NOT NULL,
    MONTH_NAME    VARCHAR(9)  NOT NULL,
    QUARTER       NUMBER(1)   NOT NULL,
    YEAR          NUMBER(4)   NOT NULL,
    IS_WEEKEND    BOOLEAN     NOT NULL,
    IS_HOLIDAY    BOOLEAN     NOT NULL
);

INSERT INTO DIM_DATE
WITH dates AS (
    SELECT DATEADD('day', SEQ4(), '2023-01-01'::DATE) AS d
    FROM TABLE(GENERATOR(rowcount => 730))
)
SELECT
    TO_NUMBER(TO_CHAR(d, 'YYYYMMDD'))                                AS DATE_KEY,
    d                                                                 AS FULL_DATE,
    DAYOFWEEK(d)                                                      AS DAY_OF_WEEK,
    DAYNAME(d)                                                        AS DAY_NAME,
    DAY(d)                                                            AS DAY_OF_MONTH,
    DAYOFYEAR(d)                                                      AS DAY_OF_YEAR,
    WEEKOFYEAR(d)                                                     AS WEEK_OF_YEAR,
    MONTH(d)                                                          AS MONTH_NUM,
    MONTHNAME(d)                                                      AS MONTH_NAME,
    QUARTER(d)                                                        AS QUARTER,
    YEAR(d)                                                           AS YEAR,
    DAYOFWEEK(d) IN (0, 6)                                           AS IS_WEEKEND,
    d IN ('2023-01-01'::DATE, '2023-07-04'::DATE, '2023-11-23'::DATE,
          '2023-12-25'::DATE, '2024-01-01'::DATE, '2024-07-04'::DATE,
          '2024-11-28'::DATE, '2024-12-25'::DATE)                    AS IS_HOLIDAY
FROM dates;

-- =============================================================================
-- 2. DIM_CUSTOMER  — 500 rows  (~5 % null BIRTH_DATE, intentional)
-- =============================================================================
CREATE OR REPLACE TABLE DIM_CUSTOMER (
    CUSTOMER_KEY      NUMBER       NOT NULL  PRIMARY KEY,
    CUSTOMER_ID       VARCHAR(12)  NOT NULL  UNIQUE,
    FIRST_NAME        VARCHAR(30)  NOT NULL,
    LAST_NAME         VARCHAR(30)  NOT NULL,
    EMAIL             VARCHAR(80)  NOT NULL,
    GENDER            VARCHAR(1)   NOT NULL,   -- M / F / O
    BIRTH_DATE        DATE,                    -- nullable
    CITY              VARCHAR(50),
    STATE             VARCHAR(2),
    COUNTRY           VARCHAR(30)  DEFAULT 'US',
    CUSTOMER_SEGMENT  VARCHAR(10)  NOT NULL,   -- Bronze Silver Gold Platinum
    REGISTRATION_DATE DATE         NOT NULL
);

INSERT INTO DIM_CUSTOMER
WITH src AS (
    SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
    FROM TABLE(GENERATOR(rowcount => 500))
),
base AS (
    SELECT
        rn,
        SPLIT_PART('James:Mary:John:Patricia:Robert:Jennifer:Michael:Linda:William:Barbara:David:Elizabeth:Richard:Susan:Joseph:Jessica:Thomas:Sarah:Charles:Karen', ':', MOD(rn - 1, 20) + 1) AS fn,
        SPLIT_PART('Smith:Johnson:Williams:Brown:Jones:Garcia:Miller:Davis:Rodriguez:Martinez:Hernandez:Lopez:Gonzalez:Wilson:Anderson:Thomas:Taylor:Moore:Jackson:Martin:Lee:Thompson:White:Harris:Sanchez:Clark', ':', MOD(rn * 7 + 2, 26) + 1) AS ln,
        SPLIT_PART('New York:Los Angeles:Chicago:Houston:Phoenix:Philadelphia:Seattle:Denver:Nashville:Miami', ':', MOD(rn * 3, 10) + 1) AS city,
        SPLIT_PART('NY:CA:IL:TX:AZ:PA:WA:CO:TN:FL', ':', MOD(rn * 3, 10) + 1) AS state
    FROM src
)
SELECT
    rn                                                                AS CUSTOMER_KEY,
    'CUST' || LPAD(rn, 6, '0')                                      AS CUSTOMER_ID,
    fn                                                                AS FIRST_NAME,
    ln                                                                AS LAST_NAME,
    LOWER(fn) || '.' || LOWER(ln) || rn || '@example.com'           AS EMAIL,
    CASE MOD(rn, 3) WHEN 0 THEN 'M' WHEN 1 THEN 'F' ELSE 'O' END   AS GENDER,
    IFF(MOD(rn, 20) = 0, NULL,
        DATEADD('day', -(UNIFORM(18 * 365, 70 * 365, RANDOM()))::INT, '2023-01-01'::DATE)) AS BIRTH_DATE,
    city                                                              AS CITY,
    state                                                             AS STATE,
    'US'                                                              AS COUNTRY,
    CASE
        WHEN MOD(rn, 10) = 0          THEN 'Platinum'
        WHEN MOD(rn, 10) IN (1, 2)   THEN 'Gold'
        WHEN MOD(rn, 10) IN (3, 4, 5) THEN 'Silver'
        ELSE                               'Bronze'
    END                                                               AS CUSTOMER_SEGMENT,
    DATEADD('day', -(UNIFORM(30, 1095, RANDOM()))::INT, '2023-01-01'::DATE) AS REGISTRATION_DATE
FROM base;

-- =============================================================================
-- 3. DIM_PRODUCT  — 200 rows across 5 categories
-- =============================================================================
CREATE OR REPLACE TABLE DIM_PRODUCT (
    PRODUCT_KEY   NUMBER        NOT NULL  PRIMARY KEY,
    PRODUCT_ID    VARCHAR(12)   NOT NULL  UNIQUE,
    PRODUCT_NAME  VARCHAR(100)  NOT NULL,
    CATEGORY      VARCHAR(30)   NOT NULL,
    SUBCATEGORY   VARCHAR(30)   NOT NULL,
    BRAND         VARCHAR(30)   NOT NULL,
    UNIT_COST     NUMBER(10,2)  NOT NULL,
    UNIT_PRICE    NUMBER(10,2)  NOT NULL,
    MARGIN_PCT    NUMBER(5,2)   NOT NULL,
    IS_ACTIVE     BOOLEAN       NOT NULL  DEFAULT TRUE
);

INSERT INTO DIM_PRODUCT
WITH src AS (
    SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
    FROM TABLE(GENERATOR(rowcount => 200))
),
cats AS (
    SELECT
        rn,
        CASE
            WHEN rn <=  50 THEN 'Electronics'
            WHEN rn <= 100 THEN 'Clothing'
            WHEN rn <= 140 THEN 'Home & Living'
            WHEN rn <= 170 THEN 'Sports'
            ELSE                'Food & Beverage'
        END AS category,
        CASE
            WHEN rn <=  10 THEN 'Smartphones'  WHEN rn <=  20 THEN 'Laptops'
            WHEN rn <=  30 THEN 'Tablets'       WHEN rn <=  40 THEN 'Headphones'
            WHEN rn <=  50 THEN 'Cameras'       WHEN rn <=  65 THEN 'T-Shirts'
            WHEN rn <=  80 THEN 'Jeans'         WHEN rn <=  90 THEN 'Dresses'
            WHEN rn <= 100 THEN 'Footwear'      WHEN rn <= 115 THEN 'Furniture'
            WHEN rn <= 125 THEN 'Kitchen'       WHEN rn <= 135 THEN 'Bath & Bedding'
            WHEN rn <= 140 THEN 'Decor'         WHEN rn <= 155 THEN 'Fitness'
            WHEN rn <= 170 THEN 'Outdoor Gear'  WHEN rn <= 185 THEN 'Coffee & Tea'
            ELSE                'Snacks'
        END AS subcategory,
        SPLIT_PART(
            CASE
                WHEN rn <=  50 THEN 'TechPro:NovaTech:EliteSmart:ApexDigital:CoreTech'
                WHEN rn <= 100 THEN 'UrbanStyle:FashionX:TrendWear:ModernFit:ClassicLine'
                WHEN rn <= 140 THEN 'HomeComfort:LivingPlus:CozySpace:UrbanNest:NestWell'
                WHEN rn <= 170 THEN 'ActivePeak:ProSport:OutdoorEdge:StrideOn:FitMax'
                ELSE                'NaturaBite:FreshPick:OrganicJoy:NutriBlend:TasteBest'
            END, ':', MOD(rn - 1, 5) + 1) AS brand,
        ROUND(CASE
            WHEN rn <=  10 THEN UNIFORM(300.0,  800.0, RANDOM())
            WHEN rn <=  20 THEN UNIFORM(500.0, 1200.0, RANDOM())
            WHEN rn <=  30 THEN UNIFORM(200.0,  600.0, RANDOM())
            WHEN rn <=  40 THEN UNIFORM( 50.0,  300.0, RANDOM())
            WHEN rn <=  50 THEN UNIFORM(100.0,  500.0, RANDOM())
            WHEN rn <=  80 THEN UNIFORM(  8.0,   60.0, RANDOM())
            WHEN rn <= 100 THEN UNIFORM( 15.0,  120.0, RANDOM())
            WHEN rn <= 140 THEN UNIFORM( 15.0,  300.0, RANDOM())
            WHEN rn <= 170 THEN UNIFORM( 15.0,  200.0, RANDOM())
            ELSE                UNIFORM(  2.0,   30.0, RANDOM())
        END::FLOAT, 2) AS unit_cost,
        ROUND(UNIFORM(1.25, 1.80, RANDOM())::FLOAT, 4) AS markup
    FROM src
)
SELECT
    rn                                                                AS PRODUCT_KEY,
    'PROD' || LPAD(rn, 6, '0')                                      AS PRODUCT_ID,
    brand || ' ' || subcategory || ' #' || LPAD(MOD(rn - 1, 30) + 1, 3, '0') AS PRODUCT_NAME,
    category                                                          AS CATEGORY,
    subcategory                                                       AS SUBCATEGORY,
    brand                                                             AS BRAND,
    unit_cost                                                         AS UNIT_COST,
    ROUND(unit_cost * markup, 2)                                     AS UNIT_PRICE,
    ROUND((1.0 - 1.0 / markup) * 100, 2)                            AS MARGIN_PCT,
    TRUE                                                              AS IS_ACTIVE
FROM cats;

-- =============================================================================
-- 4. DIM_STORE  — 50 rows across 10 US cities
-- =============================================================================
CREATE OR REPLACE TABLE DIM_STORE (
    STORE_KEY      NUMBER       NOT NULL  PRIMARY KEY,
    STORE_ID       VARCHAR(10)  NOT NULL  UNIQUE,
    STORE_NAME     VARCHAR(60)  NOT NULL,
    CITY           VARCHAR(50)  NOT NULL,
    STATE          VARCHAR(2)   NOT NULL,
    REGION         VARCHAR(20)  NOT NULL,
    STORE_TYPE     VARCHAR(10)  NOT NULL,   -- Flagship Standard Outlet
    OPEN_DATE      DATE         NOT NULL,
    SQUARE_FOOTAGE NUMBER(6)
);

INSERT INTO DIM_STORE
WITH src AS (
    SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
    FROM TABLE(GENERATOR(rowcount => 50))
),
geo AS (
    SELECT
        rn,
        SPLIT_PART('New York:Los Angeles:Chicago:Houston:Phoenix:Philadelphia:Seattle:Denver:Nashville:Miami', ':', MOD(rn - 1, 10) + 1) AS city,
        SPLIT_PART('NY:CA:IL:TX:AZ:PA:WA:CO:TN:FL',                                                          ':', MOD(rn - 1, 10) + 1) AS state,
        SPLIT_PART('Northeast:West:Midwest:South:Southwest:Northeast:West:Mountain:South:Southeast',          ':', MOD(rn - 1, 10) + 1) AS region,
        CASE MOD(rn - 1, 3) WHEN 0 THEN 'Flagship' WHEN 1 THEN 'Standard' ELSE 'Outlet' END AS store_type
    FROM src
)
SELECT
    rn                                                                AS STORE_KEY,
    'STR' || LPAD(rn, 4, '0')                                       AS STORE_ID,
    store_type || ' — ' || city                                      AS STORE_NAME,
    city, state, region, store_type,
    DATEADD('day', -(UNIFORM(365, 3650, RANDOM()))::INT, '2023-01-01'::DATE) AS OPEN_DATE,
    CASE store_type
        WHEN 'Flagship' THEN UNIFORM(8000, 15000, RANDOM())::INT
        WHEN 'Standard' THEN UNIFORM(3000,  7000, RANDOM())::INT
        ELSE                 UNIFORM(1500,  3500, RANDOM())::INT
    END                                                               AS SQUARE_FOOTAGE
FROM geo;

-- =============================================================================
-- 5. DIM_EMPLOYEE  — 100 rows  (~5 % inactive)
-- =============================================================================
CREATE OR REPLACE TABLE DIM_EMPLOYEE (
    EMPLOYEE_KEY  NUMBER       NOT NULL  PRIMARY KEY,
    EMPLOYEE_ID   VARCHAR(10)  NOT NULL  UNIQUE,
    FIRST_NAME    VARCHAR(30)  NOT NULL,
    LAST_NAME     VARCHAR(30)  NOT NULL,
    JOB_TITLE     VARCHAR(40)  NOT NULL,
    DEPARTMENT    VARCHAR(30)  NOT NULL,
    STORE_KEY     NUMBER       NOT NULL,   -- FK → DIM_STORE
    HIRE_DATE     DATE         NOT NULL,
    IS_ACTIVE     BOOLEAN      NOT NULL  DEFAULT TRUE
);

INSERT INTO DIM_EMPLOYEE
WITH src AS (
    SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
    FROM TABLE(GENERATOR(rowcount => 100))
)
SELECT
    rn                                                                AS EMPLOYEE_KEY,
    'EMP' || LPAD(rn, 5, '0')                                       AS EMPLOYEE_ID,
    SPLIT_PART('James:Mary:John:Patricia:Robert:Jennifer:Michael:Linda:William:Barbara:David:Elizabeth:Richard:Susan:Joseph:Jessica:Thomas:Sarah:Charles:Karen', ':', MOD(rn - 1, 20) + 1) AS FIRST_NAME,
    SPLIT_PART('Smith:Johnson:Williams:Brown:Jones:Garcia:Miller:Davis:Rodriguez:Martinez:Hernandez:Lopez:Gonzalez:Wilson:Anderson:Thomas:Taylor:Moore:Jackson:Martin', ':', MOD(rn * 11 + 3, 20) + 1) AS LAST_NAME,
    CASE MOD(rn - 1, 6)
        WHEN 0 THEN 'Sales Associate'        WHEN 1 THEN 'Senior Sales Associate'
        WHEN 2 THEN 'Department Manager'     WHEN 3 THEN 'Assistant Manager'
        WHEN 4 THEN 'Store Manager'          ELSE        'Cashier'
    END                                                               AS JOB_TITLE,
    CASE MOD(rn - 1, 4)
        WHEN 0 THEN 'Sales'   WHEN 1 THEN 'Customer Service'
        WHEN 2 THEN 'Operations' ELSE 'Management'
    END                                                               AS DEPARTMENT,
    MOD(rn - 1, 50) + 1                                              AS STORE_KEY,
    DATEADD('day', -(UNIFORM(30, 2190, RANDOM()))::INT, '2023-01-01'::DATE) AS HIRE_DATE,
    IFF(MOD(rn, 20) = 0, FALSE, TRUE)                                AS IS_ACTIVE
FROM src;

-- =============================================================================
-- 6. DIM_PROMOTION  — 30 rows
-- =============================================================================
CREATE OR REPLACE TABLE DIM_PROMOTION (
    PROMOTION_KEY    NUMBER        NOT NULL  PRIMARY KEY,
    PROMOTION_ID     VARCHAR(12)   NOT NULL  UNIQUE,
    PROMOTION_NAME   VARCHAR(60)   NOT NULL,
    PROMOTION_TYPE   VARCHAR(15)   NOT NULL,   -- Seasonal Loyalty Flash Clearance
    DISCOUNT_PERCENT NUMBER(5,2)   NOT NULL,
    START_DATE       DATE          NOT NULL,
    END_DATE         DATE          NOT NULL,
    IS_ACTIVE        BOOLEAN       NOT NULL  DEFAULT TRUE
);

INSERT INTO DIM_PROMOTION
WITH src AS (
    SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
    FROM TABLE(GENERATOR(rowcount => 30))
),
types AS (
    SELECT
        rn,
        CASE MOD(rn - 1, 4)
            WHEN 0 THEN 'Seasonal'  WHEN 1 THEN 'Loyalty'
            WHEN 2 THEN 'Flash'     ELSE        'Clearance'
        END AS promo_type,
        ROUND(CASE MOD(rn - 1, 4)
            WHEN 0 THEN UNIFORM(10.0, 25.0, RANDOM())
            WHEN 1 THEN UNIFORM( 5.0, 15.0, RANDOM())
            WHEN 2 THEN UNIFORM(20.0, 40.0, RANDOM())
            ELSE        UNIFORM(30.0, 50.0, RANDOM())
        END::FLOAT, 2) AS disc_pct,
        DATEADD('day', (rn - 1) * 24, '2023-01-01'::DATE) AS start_dt
    FROM src
)
SELECT
    rn                                                                AS PROMOTION_KEY,
    'PROMO' || LPAD(rn, 5, '0')                                     AS PROMOTION_ID,
    promo_type || ' Event #' || rn                                   AS PROMOTION_NAME,
    promo_type                                                        AS PROMOTION_TYPE,
    disc_pct                                                          AS DISCOUNT_PERCENT,
    start_dt                                                          AS START_DATE,
    DATEADD('day', UNIFORM(7, 30, RANDOM())::INT, start_dt)          AS END_DATE,
    IFF(rn <= 20, TRUE, FALSE)                                        AS IS_ACTIVE
FROM types;

-- =============================================================================
-- 7. FACT_SALES  — 10 000 rows
-- =============================================================================
CREATE OR REPLACE TABLE FACT_SALES (
    SALE_KEY        NUMBER        NOT NULL  PRIMARY KEY,
    DATE_KEY        NUMBER(8)     NOT NULL,   -- FK → DIM_DATE
    CUSTOMER_KEY    NUMBER        NOT NULL,   -- FK → DIM_CUSTOMER
    PRODUCT_KEY     NUMBER        NOT NULL,   -- FK → DIM_PRODUCT
    STORE_KEY       NUMBER        NOT NULL,   -- FK → DIM_STORE
    EMPLOYEE_KEY    NUMBER        NOT NULL,   -- FK → DIM_EMPLOYEE
    PROMOTION_KEY   NUMBER,                   -- FK → DIM_PROMOTION (nullable — ~70 % NULL)
    TRANSACTION_ID  VARCHAR(20)   NOT NULL  UNIQUE,
    PAYMENT_METHOD  VARCHAR(15)   NOT NULL,
    QUANTITY        NUMBER(3)     NOT NULL,
    UNIT_PRICE      NUMBER(10,2)  NOT NULL,
    UNIT_COST       NUMBER(10,2)  NOT NULL,
    DISCOUNT_AMOUNT NUMBER(10,2)  NOT NULL  DEFAULT 0,
    GROSS_AMOUNT    NUMBER(10,2)  NOT NULL,
    NET_AMOUNT      NUMBER(10,2)  NOT NULL,
    TAX_AMOUNT      NUMBER(10,2)  NOT NULL,
    MARGIN_AMOUNT   NUMBER(10,2)  NOT NULL
);

INSERT INTO FACT_SALES
WITH raw AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY SEQ4())                           AS rn,
        DATEADD('day', UNIFORM(0, 729, RANDOM())::INT, '2023-01-01'::DATE) AS sale_dt,
        UNIFORM(1, 500, RANDOM())::INT                                AS cust_k,
        UNIFORM(1, 200, RANDOM())::INT                                AS prod_k,
        UNIFORM(1,  50, RANDOM())::INT                                AS store_k,
        UNIFORM(1, 100, RANDOM())::INT                                AS emp_k,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.30, UNIFORM(1, 30, RANDOM())::INT, NULL) AS promo_k,
        LEAST(10, GREATEST(1, ROUND(NORMAL(2.5, 1.5, RANDOM()))::INT)) AS qty
    FROM TABLE(GENERATOR(rowcount => 10000))
),
raw2 AS (
    SELECT
        rn, sale_dt, cust_k, prod_k, store_k, emp_k, promo_k, qty,
        CASE MOD(rn, 5)
            WHEN 0 THEN 'CREDIT_CARD'  WHEN 1 THEN 'DEBIT_CARD'
            WHEN 2 THEN 'CASH'         WHEN 3 THEN 'PAYPAL'
            ELSE        'APPLE_PAY'
        END AS payment
    FROM raw
),
enriched AS (
    SELECT
        r.*,
        p.UNIT_PRICE,
        p.UNIT_COST,
        COALESCE(pr.DISCOUNT_PERCENT, 0.0) AS disc_pct
    FROM raw2 r
    JOIN      DIM_PRODUCT   p  ON p.PRODUCT_KEY   = r.prod_k
    LEFT JOIN DIM_PROMOTION pr ON pr.PROMOTION_KEY = r.promo_k
)
SELECT
    rn                                                                AS SALE_KEY,
    TO_NUMBER(TO_CHAR(sale_dt, 'YYYYMMDD'))                          AS DATE_KEY,
    cust_k                                                            AS CUSTOMER_KEY,
    prod_k                                                            AS PRODUCT_KEY,
    store_k                                                           AS STORE_KEY,
    emp_k                                                             AS EMPLOYEE_KEY,
    promo_k                                                           AS PROMOTION_KEY,
    'TXN' || LPAD(rn, 8, '0')                                        AS TRANSACTION_ID,
    payment                                                           AS PAYMENT_METHOD,
    qty                                                               AS QUANTITY,
    UNIT_PRICE,
    UNIT_COST,
    ROUND(UNIT_PRICE * qty * disc_pct / 100.0, 2)                   AS DISCOUNT_AMOUNT,
    ROUND(UNIT_PRICE * qty, 2)                                       AS GROSS_AMOUNT,
    ROUND(UNIT_PRICE * qty * (1.0 - disc_pct / 100.0), 2)           AS NET_AMOUNT,
    ROUND(UNIT_PRICE * qty * (1.0 - disc_pct / 100.0) * 0.08, 2)    AS TAX_AMOUNT,
    ROUND((UNIT_PRICE - UNIT_COST) * qty * (1.0 - disc_pct / 100.0), 2) AS MARGIN_AMOUNT
FROM enriched;

-- =============================================================================
-- 8. Verification
-- =============================================================================

-- Row counts
SELECT 'DIM_DATE'       AS table_name, COUNT(*) AS row_count FROM DIM_DATE
UNION ALL SELECT 'DIM_CUSTOMER',  COUNT(*) FROM DIM_CUSTOMER
UNION ALL
SELECT 'DIM_PRODUCT', COUNT(*) FROM DIM_PRODUCT
UNION ALL
SELECT 'DIM_STORE', COUNT(*) FROM DIM_STORE
UNION ALL
SELECT 'DIM_EMPLOYEE', COUNT(*) FROM DIM_EMPLOYEE
UNION ALL
SELECT 'DIM_PROMOTION', COUNT(*) FROM DIM_PROMOTION
UNION ALL
SELECT 'FACT_SALES', COUNT(*) FROM FACT_SALES;

-- FK integrity — each query must return 0 orphans
SELECT 'orphan_dates'     AS check_name, COUNT(*) AS orphans FROM FACT_SALES f WHERE NOT EXISTS (SELECT 1 FROM DIM_DATE      d WHERE d.DATE_KEY      = f.DATE_KEY)
UNION ALL SELECT 'orphan_customers',     COUNT(*) FROM FACT_SALES f WHERE NOT EXISTS (SELECT 1 FROM DIM_CUSTOMER  c WHERE c.CUSTOMER_KEY  = f.CUSTOMER_KEY)
UNION ALL SELECT 'orphan_products',      COUNT(*) FROM FACT_SALES f WHERE NOT EXISTS (SELECT 1 FROM DIM_PRODUCT   p WHERE p.PRODUCT_KEY   = f.PRODUCT_KEY)
UNION ALL SELECT 'orphan_stores',        COUNT(*) FROM FACT_SALES f WHERE NOT EXISTS (SELECT 1 FROM DIM_STORE     s WHERE s.STORE_KEY     = f.STORE_KEY)
UNION ALL SELECT 'orphan_employees',     COUNT(*) FROM FACT_SALES f WHERE NOT EXISTS (SELECT 1 FROM DIM_EMPLOYEE  e WHERE e.EMPLOYEE_KEY  = f.EMPLOYEE_KEY);

-- Revenue by year / quarter
SELECT
    d.YEAR,
    d.QUARTER,
    COUNT(*)                                                          AS num_transactions,
    SUM(f.QUANTITY)                                                   AS units_sold,
    ROUND(SUM(f.GROSS_AMOUNT), 2)                                    AS gross_revenue,
    ROUND(SUM(f.NET_AMOUNT), 2)                                      AS net_revenue,
    ROUND(SUM(f.MARGIN_AMOUNT), 2)                                   AS total_margin,
    ROUND(SUM(f.MARGIN_AMOUNT) / NULLIF(SUM(f.NET_AMOUNT), 0) * 100, 2) AS margin_pct
FROM FACT_SALES f
JOIN DIM_DATE d ON d.DATE_KEY = f.DATE_KEY
GROUP BY 1, 2
ORDER BY 1, 2;

-- Top 10 products by revenue
SELECT
    p.PRODUCT_NAME,
    p.CATEGORY,
    COUNT(*)                       AS num_sales,
    SUM(f.QUANTITY)                AS units_sold,
    ROUND(SUM(f.NET_AMOUNT), 2)   AS net_revenue
FROM FACT_SALES f
JOIN DIM_PRODUCT p ON p.PRODUCT_KEY = f.PRODUCT_KEY
GROUP BY 1, 2
ORDER BY net_revenue DESC
LIMIT 10;

-- Customer segment breakdown
SELECT
    c.CUSTOMER_SEGMENT,
    COUNT(DISTINCT f.CUSTOMER_KEY)          AS unique_customers,
    COUNT(*)                                AS num_transactions,
    ROUND(AVG(f.NET_AMOUNT), 2)            AS avg_order_value,
    ROUND(SUM(f.NET_AMOUNT), 2)            AS total_revenue
FROM FACT_SALES f
JOIN DIM_CUSTOMER c ON c.CUSTOMER_KEY = f.CUSTOMER_KEY
GROUP BY 1
ORDER BY total_revenue DESC;

-- =============================================================================
-- 9. Update config.yaml to point the profiler at this database
-- =============================================================================
--
--  snowflake:
--    database: SAMPLE_DW
--    schema:   RETAIL
--
--  Then open http://localhost:5000/profile and select any of:
--    FACT_SALES, DIM_CUSTOMER, DIM_PRODUCT, DIM_STORE,
--    DIM_EMPLOYEE, DIM_PROMOTION, DIM_DATE
-- =============================================================================
