-- =============================================================================
-- SETUP_ORGANIC_SALES.SQL
-- Organic 10M+ Retail Sales — SAMPLE_DW.ORGANIC
-- =============================================================================
--
--  Schema: SAMPLE_DW.ORGANIC   (run AFTER setup_snowflake.sql)
--
--  DIM_DATE      DIM_CUSTOMER    DIM_PROMOTION   DIM_CHANNEL (NEW)
--      |              |                |               |
--      └──────────────┴────────────────┴───────────────┘
--                                |
--                    FACT_SALES_ORGANIC
--                                |
--          ┌─────────────────────┼──────────────────────┐
--      DIM_PRODUCT         DIM_STORE              DIM_EMPLOYEE
--
--  Organic distribution rules implemented
--  ─────────────────────────────────────────────────────────────
--  1. SEASONAL DATE DISTRIBUTION
--     Records are NOT uniformly spread across 730 days.
--     Each block below targets a different part of the year so that
--     the resulting fact table shows realistic retail seasonality:
--
--       Block   Months               Records   Avg/day   vs avg
--       ─────   ────────────────     ───────   ───────   ──────
--       Q1-23   Jan–Mar  2023        1,200,000   13,333   -15%
--       Q2-23   Apr–Jun  2023        1,800,000   20,000   +27%
--       Q3-23   Jul–Sep  2023        2,300,000   25,556   +62%
--       Q4-23   Oct–Dec  2023        3,500,000   38,889  +147%  ← holiday peak
--       H1-24   Jan–Jun  2024        1,500,000   16,667    +6%   (YoY growth)
--       H2-24   Jul–Dec  2024        1,700,000   18,889   +20%  (YoY growth)
--       ─────                       ──────────
--       TOTAL                       12,000,000
--
--  2. DAY-OF-WEEK EFFECT
--     Weekend transactions carry +30% quantity multiplier.
--
--  3. PRODUCT-SEASON AFFINITY
--     Electronics: 40% weight in Q4, 20% in other quarters
--     Sports:      30% weight in Q3 (summer), 12% other
--     Clothing:    25% weight in Q2/Q3 (fashion season)
--     Home:        20% weight in Q2 (spring), 10% other
--     Food/Bev:    steady ~15% all year
--
--  4. CUSTOMER FREQUENCY (PARETO / 80-20 EFFECT)
--     Top 20% of customers (Platinum + Gold) generate ~60% of transactions.
--
--  5. CHANNEL DISTRIBUTION (new DIM_CHANNEL dimension)
--     Online channel grows 2023→2024 (+40% YoY).
--
--  6. PROMOTION ANTI-CORRELATION WITH PEAK DEMAND
--     Q4 holiday peak: 18% promo rate (high demand = fewer discounts needed)
--     Q1 clearance:    48% promo rate (slow season → aggressive discounting)
--
--  Table               Rows
--  ──────────────────  ─────────
--  DIM_DATE              730   (2023-01-01 to 2024-12-31, reused from RETAIL)
--  DIM_CUSTOMER        10,000
--  DIM_PRODUCT            800  (5 categories, expanded SKU count)
--  DIM_STORE              200  (50 cities, 4 formats incl. Online)
--  DIM_EMPLOYEE           600
--  DIM_PROMOTION          200
--  DIM_CHANNEL              5  (NEW: In-Store / Web / Mobile / Call / Partner)
--  FACT_SALES_ORGANIC  12,000,000
-- =============================================================================
-- ► Edit the warehouse name below before running
-- =============================================================================
USE WAREHOUSE PROFILER_APP;

CREATE DATABASE  IF NOT EXISTS SAMPLE_DW;
CREATE SCHEMA    IF NOT EXISTS SAMPLE_DW.ORGANIC;
USE DATABASE SAMPLE_DW;
USE SCHEMA   ORGANIC;

-- =============================================================================
-- 0. Shared holidays set (used in DIM_DATE and FACT derived columns)
-- =============================================================================
-- 2023: Jan 1, Jan 16 (MLK), Feb 20 (Presidents), May 29 (Memorial),
--       Jul 4, Sep 4 (Labor), Nov 23 (Thanksgiving), Nov 24 (Black Friday),
--       Dec 24 (Christmas Eve), Dec 25
-- 2024: same pattern +1 day for floating holidays

-- =============================================================================
-- 1. DIM_DATE  — 730 rows (2023-01-01 to 2024-12-31)
-- =============================================================================
CREATE OR REPLACE TABLE DIM_DATE (
    DATE_KEY      NUMBER(8)   NOT NULL  PRIMARY KEY,
    FULL_DATE     DATE        NOT NULL,
    DAY_OF_WEEK   NUMBER(1)   NOT NULL,
    DAY_NAME      VARCHAR(9)  NOT NULL,
    DAY_OF_MONTH  NUMBER(2)   NOT NULL,
    DAY_OF_YEAR   NUMBER(3)   NOT NULL,
    WEEK_OF_YEAR  NUMBER(2)   NOT NULL,
    MONTH_NUM     NUMBER(2)   NOT NULL,
    MONTH_NAME    VARCHAR(9)  NOT NULL,
    QUARTER       NUMBER(1)   NOT NULL,
    YEAR          NUMBER(4)   NOT NULL,
    IS_WEEKEND    BOOLEAN     NOT NULL,
    IS_HOLIDAY    BOOLEAN     NOT NULL,
    IS_BLACK_FRIDAY BOOLEAN   NOT NULL,
    SEASON        VARCHAR(10) NOT NULL    -- Spring Summer Fall Winter
) COMMENT = 'Date dimension 2023-01-01 to 2024-12-31 with retail holiday markers';

INSERT INTO DIM_DATE
WITH dates AS (
    SELECT DATEADD('day', SEQ4(), '2023-01-01'::DATE) AS d
    FROM TABLE(GENERATOR(rowcount => 730))
)
SELECT
    TO_NUMBER(TO_CHAR(d, 'YYYYMMDD'))   AS DATE_KEY,
    d                                    AS FULL_DATE,
    DAYOFWEEK(d)                         AS DAY_OF_WEEK,
    DAYNAME(d)                           AS DAY_NAME,
    DAY(d)                               AS DAY_OF_MONTH,
    DAYOFYEAR(d)                         AS DAY_OF_YEAR,
    WEEKOFYEAR(d)                        AS WEEK_OF_YEAR,
    MONTH(d)                             AS MONTH_NUM,
    MONTHNAME(d)                         AS MONTH_NAME,
    QUARTER(d)                           AS QUARTER,
    YEAR(d)                              AS YEAR,
    DAYOFWEEK(d) IN (0, 6)              AS IS_WEEKEND,
    d IN (
        '2023-01-01'::DATE, '2023-01-16'::DATE, '2023-02-20'::DATE,
        '2023-05-29'::DATE, '2023-07-04'::DATE, '2023-09-04'::DATE,
        '2023-11-23'::DATE, '2023-11-24'::DATE, '2023-12-24'::DATE,
        '2023-12-25'::DATE, '2024-01-01'::DATE, '2024-01-15'::DATE,
        '2024-02-19'::DATE, '2024-05-27'::DATE, '2024-07-04'::DATE,
        '2024-09-02'::DATE, '2024-11-28'::DATE, '2024-11-29'::DATE,
        '2024-12-24'::DATE, '2024-12-25'::DATE
    )                                    AS IS_HOLIDAY,
    d IN ('2023-11-24'::DATE, '2024-11-29'::DATE)
                                         AS IS_BLACK_FRIDAY,
    CASE MONTH(d)
        WHEN 12 THEN 'Winter' WHEN 1 THEN 'Winter' WHEN 2 THEN 'Winter'
        WHEN  3 THEN 'Spring' WHEN 4 THEN 'Spring' WHEN 5 THEN 'Spring'
        WHEN  6 THEN 'Summer' WHEN 7 THEN 'Summer' WHEN 8 THEN 'Summer'
        ELSE 'Fall'
    END                                  AS SEASON
FROM dates;

-- =============================================================================
-- 2. DIM_CHANNEL  — 5 rows  (NEW dimension)
-- =============================================================================
CREATE OR REPLACE TABLE DIM_CHANNEL (
    CHANNEL_KEY   NUMBER      NOT NULL  PRIMARY KEY,
    CHANNEL_ID    VARCHAR(10) NOT NULL  UNIQUE,
    CHANNEL_NAME  VARCHAR(30) NOT NULL,
    CHANNEL_TYPE  VARCHAR(15) NOT NULL,   -- Digital | Physical | Partner
    IS_DIGITAL    BOOLEAN     NOT NULL,
    BASE_AOV_MULTIPLIER NUMBER(4,2) NOT NULL  -- avg order value vs baseline
) COMMENT = 'Sales channel dimension — omnichannel retail';

INSERT INTO DIM_CHANNEL VALUES
    (1, 'CH-STORE',  'Physical Store',    'Physical', FALSE, 1.00),
    (2, 'CH-WEB',    'Online Web',        'Digital',  TRUE,  1.15),
    (3, 'CH-MOBILE', 'Mobile App',        'Digital',  TRUE,  0.95),
    (4, 'CH-CALL',   'Call Center',       'Physical', FALSE, 1.05),
    (5, 'CH-MARKET', 'Marketplace',       'Partner',  TRUE,  0.90);

-- =============================================================================
-- 3. DIM_CUSTOMER  — 10,000 rows
--    Segments: Bronze 40%, Silver 30%, Gold 20%, Platinum 10%
--    Pareto distribution: top 20% of customers = 60% of transactions
-- =============================================================================
CREATE OR REPLACE TABLE DIM_CUSTOMER (
    CUSTOMER_KEY      NUMBER       NOT NULL  PRIMARY KEY,
    CUSTOMER_ID       VARCHAR(12)  NOT NULL  UNIQUE,
    FIRST_NAME        VARCHAR(30)  NOT NULL,
    LAST_NAME         VARCHAR(30)  NOT NULL,
    EMAIL             VARCHAR(80)  NOT NULL,
    GENDER            VARCHAR(1)   NOT NULL,
    BIRTH_DATE        DATE,
    CITY              VARCHAR(50),
    STATE             VARCHAR(2),
    REGION            VARCHAR(20),
    COUNTRY           VARCHAR(30)  DEFAULT 'US',
    CUSTOMER_SEGMENT  VARCHAR(10)  NOT NULL,
    LOYALTY_YEARS     NUMBER(3,1)  NOT NULL,   -- years as customer
    DIGITAL_AFFINITY  VARCHAR(10)  NOT NULL,   -- High Medium Low
    REGISTRATION_DATE DATE         NOT NULL
) COMMENT = 'Customer master — 10,000 records. Platinum/Gold top 30% drive majority of revenue.';

INSERT INTO DIM_CUSTOMER
WITH src AS (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
             FROM TABLE(GENERATOR(rowcount => 10000))),
geo AS (
    SELECT rn,
        SPLIT_PART(
            'New York:Los Angeles:Chicago:Houston:Phoenix:Philadelphia:Seattle:'
            'Denver:Nashville:Miami:Dallas:Atlanta:Boston:San Francisco:Portland:'
            'Las Vegas:Minneapolis:Detroit:Kansas City:Salt Lake City:Orlando:'
            'Pittsburgh:Cincinnati:St. Louis:Tampa:Charlotte:Raleigh:Baltimore:'
            'Sacramento:Indianapolis',
            ':', MOD(rn * 7 + 3, 30) + 1)  AS city,
        SPLIT_PART(
            'NY:CA:IL:TX:AZ:PA:WA:CO:TN:FL:TX:GA:MA:CA:OR:'
            'NV:MN:MI:MO:UT:FL:PA:OH:MO:FL:NC:NC:MD:CA:IN',
            ':', MOD(rn * 7 + 3, 30) + 1)  AS state,
        SPLIT_PART(
            'Northeast:West:Midwest:South:Southwest:Northeast:West:Mountain:South:Southeast:'
            'South:Southeast:Northeast:West:West:Southwest:Midwest:Midwest:Midwest:Mountain:'
            'Southeast:Northeast:Midwest:Midwest:Southeast:Southeast:Southeast:Northeast:West:Midwest',
            ':', MOD(rn * 7 + 3, 30) + 1)  AS region
    FROM src
)
SELECT
    rn                                                                 AS CUSTOMER_KEY,
    'CUST' || LPAD(rn, 8, '0')                                       AS CUSTOMER_ID,
    SPLIT_PART('James:Mary:John:Patricia:Robert:Jennifer:Michael:Linda:William:Barbara:'
               'David:Elizabeth:Richard:Susan:Joseph:Jessica:Thomas:Sarah:Charles:Karen:'
               'Christopher:Lisa:Daniel:Nancy:Matthew:Betty:Anthony:Sandra:Mark:Donna',
               ':', MOD(rn - 1, 30) + 1)                              AS FIRST_NAME,
    SPLIT_PART('Smith:Johnson:Williams:Brown:Jones:Garcia:Miller:Davis:Rodriguez:Martinez:'
               'Hernandez:Lopez:Gonzalez:Wilson:Anderson:Thomas:Taylor:Moore:Jackson:Martin:'
               'Lee:Thompson:White:Harris:Sanchez:Clark:Ramirez:Lewis:Robinson:Walker',
               ':', MOD(rn * 11 + 5, 30) + 1)                         AS LAST_NAME,
    LOWER(SPLIT_PART('james:mary:john:patricia:robert:jennifer:michael:linda:william:barbara:'
                     'david:elizabeth:richard:susan:joseph:jessica:thomas:sarah:charles:karen:'
                     'christopher:lisa:daniel:nancy:matthew:betty:anthony:sandra:mark:donna',
                     ':', MOD(rn - 1, 30) + 1))
        || rn || '@example.com'                                        AS EMAIL,
    CASE MOD(rn, 3) WHEN 0 THEN 'M' WHEN 1 THEN 'F' ELSE 'O' END    AS GENDER,
    IFF(MOD(rn, 25) = 0, NULL,
        DATEADD('day', -(UNIFORM(18*365, 75*365, RANDOM()))::INT,
                '2023-01-01'::DATE))                                   AS BIRTH_DATE,
    city AS CITY, state AS STATE, region AS REGION, 'US' AS COUNTRY,
    -- Pareto distribution: 10% Platinum, 20% Gold, 30% Silver, 40% Bronze
    CASE
        WHEN rn <= 1000  THEN 'Platinum'    -- top 10%: highest purchase freq
        WHEN rn <= 3000  THEN 'Gold'
        WHEN rn <= 6000  THEN 'Silver'
        ELSE                  'Bronze'
    END                                                                AS CUSTOMER_SEGMENT,
    ROUND(UNIFORM(0.5, 8.0, RANDOM())::FLOAT, 1)                     AS LOYALTY_YEARS,
    CASE MOD(rn, 10)
        WHEN 0 THEN 'High'   WHEN 1 THEN 'High'   WHEN 2 THEN 'High'
        WHEN 3 THEN 'Medium' WHEN 4 THEN 'Medium' WHEN 5 THEN 'Medium'
        WHEN 6 THEN 'Medium' ELSE 'Low'
    END                                                                AS DIGITAL_AFFINITY,
    DATEADD('day', -(UNIFORM(30, 2920, RANDOM()))::INT,
            '2023-01-01'::DATE)                                        AS REGISTRATION_DATE
FROM geo;

-- =============================================================================
-- 4. DIM_PRODUCT  — 800 rows across 5 categories
--    Category key ranges (used by seasonal affinity logic in FACT):
--      Electronics  1 – 200   (luxury/tech → low qty, high price)
--      Clothing   201 – 400   (fashion → medium qty)
--      Home       401 – 560   (durable → medium qty)
--      Sports     561 – 700   (leisure → medium qty)
--      Food/Bev   701 – 800   (consumable → HIGH qty)
-- =============================================================================
CREATE OR REPLACE TABLE DIM_PRODUCT (
    PRODUCT_KEY     NUMBER        NOT NULL  PRIMARY KEY,
    PRODUCT_ID      VARCHAR(12)   NOT NULL  UNIQUE,
    PRODUCT_NAME    VARCHAR(100)  NOT NULL,
    CATEGORY        VARCHAR(30)   NOT NULL,
    SUBCATEGORY     VARCHAR(30)   NOT NULL,
    BRAND           VARCHAR(30)   NOT NULL,
    TIER            VARCHAR(10)   NOT NULL,   -- Premium Standard Budget
    UNIT_COST       NUMBER(10,2)  NOT NULL,
    UNIT_PRICE      NUMBER(10,2)  NOT NULL,
    MARGIN_PCT      NUMBER(5,2)   NOT NULL,
    IS_ACTIVE       BOOLEAN       NOT NULL  DEFAULT TRUE,
    IS_SEASONAL     BOOLEAN       NOT NULL  DEFAULT FALSE
) COMMENT = 'Product catalog — 800 SKUs. Key ranges encode categories for seasonal affinity queries.';

INSERT INTO DIM_PRODUCT
WITH src AS (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
             FROM TABLE(GENERATOR(rowcount => 800))),
cats AS (
    SELECT rn,
        CASE
            WHEN rn <= 200 THEN 'Electronics'
            WHEN rn <= 400 THEN 'Clothing'
            WHEN rn <= 560 THEN 'Home & Living'
            WHEN rn <= 700 THEN 'Sports'
            ELSE                'Food & Beverage'
        END AS category,
        CASE
            WHEN rn <=  40 THEN 'Smartphones'   WHEN rn <=  80 THEN 'Laptops'
            WHEN rn <= 110 THEN 'Tablets'        WHEN rn <= 140 THEN 'Headphones'
            WHEN rn <= 165 THEN 'Cameras'        WHEN rn <= 200 THEN 'Smart Home'
            WHEN rn <= 240 THEN 'T-Shirts'       WHEN rn <= 280 THEN 'Jeans'
            WHEN rn <= 320 THEN 'Dresses'        WHEN rn <= 360 THEN 'Jackets'
            WHEN rn <= 400 THEN 'Footwear'       WHEN rn <= 440 THEN 'Furniture'
            WHEN rn <= 480 THEN 'Kitchen'        WHEN rn <= 520 THEN 'Bath & Bedding'
            WHEN rn <= 560 THEN 'Decor'          WHEN rn <= 620 THEN 'Fitness Equipment'
            WHEN rn <= 660 THEN 'Outdoor Gear'   WHEN rn <= 700 THEN 'Team Sports'
            WHEN rn <= 740 THEN 'Coffee & Tea'   WHEN rn <= 770 THEN 'Snacks'
            ELSE                'Supplements'
        END AS subcategory,
        SPLIT_PART(
            CASE
                WHEN rn <= 200 THEN 'TechPro:NovaTech:EliteSmart:ApexDigital:CoreTech:ZenTech:QuantumX'
                WHEN rn <= 400 THEN 'UrbanStyle:FashionX:TrendWear:ModernFit:ClassicLine:VogueEdge:StyleHub'
                WHEN rn <= 560 THEN 'HomeComfort:LivingPlus:CozySpace:UrbanNest:NestWell:CraftHome:ElegantSpace'
                WHEN rn <= 700 THEN 'ActivePeak:ProSport:OutdoorEdge:StrideOn:FitMax:NaturePath:ApexAthletics'
                ELSE                'NaturaBite:FreshPick:OrganicJoy:NutriBlend:TasteBest:PureOrigins:GreenLeaf'
            END, ':', MOD(rn - 1, 7) + 1)  AS brand,
        CASE MOD(rn, 3)
            WHEN 0 THEN 'Premium'
            WHEN 1 THEN 'Standard'
            ELSE        'Budget'
        END AS tier,
        ROUND(CASE
            WHEN rn <=  40  THEN UNIFORM(300.0, 900.0,  RANDOM())
            WHEN rn <=  80  THEN UNIFORM(500.0, 1400.0, RANDOM())
            WHEN rn <= 110  THEN UNIFORM(200.0, 700.0,  RANDOM())
            WHEN rn <= 140  THEN UNIFORM(40.0,  350.0,  RANDOM())
            WHEN rn <= 165  THEN UNIFORM(80.0,  600.0,  RANDOM())
            WHEN rn <= 200  THEN UNIFORM(30.0,  200.0,  RANDOM())
            WHEN rn <= 280  THEN UNIFORM(6.0,   70.0,   RANDOM())
            WHEN rn <= 360  THEN UNIFORM(12.0,  130.0,  RANDOM())
            WHEN rn <= 400  THEN UNIFORM(18.0,  160.0,  RANDOM())
            WHEN rn <= 480  THEN UNIFORM(12.0,  280.0,  RANDOM())
            WHEN rn <= 560  THEN UNIFORM(8.0,   120.0,  RANDOM())
            WHEN rn <= 660  THEN UNIFORM(15.0,  220.0,  RANDOM())
            WHEN rn <= 700  THEN UNIFORM(8.0,   80.0,   RANDOM())
            WHEN rn <= 770  THEN UNIFORM(2.0,   28.0,   RANDOM())
            ELSE                 UNIFORM(10.0,  60.0,   RANDOM())
        END::FLOAT, 2)  AS unit_cost,
        ROUND(UNIFORM(1.20, 1.90, RANDOM())::FLOAT, 4)  AS markup,
        rn IN (
            SELECT UNIFORM(1, 800, RANDOM())::INT FROM TABLE(GENERATOR(rowcount => 120))
        )  AS is_seasonal
    FROM src
)
SELECT
    rn                                                                   AS PRODUCT_KEY,
    'PROD' || LPAD(rn, 7, '0')                                         AS PRODUCT_ID,
    brand || ' ' || subcategory || ' #' || LPAD(MOD(rn - 1, 40) + 1, 3, '0')
                                                                         AS PRODUCT_NAME,
    category, subcategory, brand, tier,
    unit_cost                                                            AS UNIT_COST,
    ROUND(unit_cost * markup, 2)                                        AS UNIT_PRICE,
    ROUND((1.0 - 1.0 / markup) * 100, 2)                               AS MARGIN_PCT,
    TRUE                                                                 AS IS_ACTIVE,
    is_seasonal                                                          AS IS_SEASONAL
FROM cats;

-- =============================================================================
-- 5. DIM_STORE  — 200 rows (50 cities × 4 formats)
-- =============================================================================
CREATE OR REPLACE TABLE DIM_STORE (
    STORE_KEY      NUMBER       NOT NULL  PRIMARY KEY,
    STORE_ID       VARCHAR(10)  NOT NULL  UNIQUE,
    STORE_NAME     VARCHAR(70)  NOT NULL,
    CITY           VARCHAR(50)  NOT NULL,
    STATE          VARCHAR(2)   NOT NULL,
    REGION         VARCHAR(20)  NOT NULL,
    STORE_TYPE     VARCHAR(12)  NOT NULL,   -- Flagship Standard Outlet Online
    IS_ONLINE      BOOLEAN      NOT NULL,
    OPEN_DATE      DATE         NOT NULL,
    SQUARE_FOOTAGE NUMBER(6),
    TIER           VARCHAR(10)  NOT NULL    -- Tier1 Tier2 Tier3
) COMMENT = 'Store dimension — 200 locations across 50 US cities, incl. online channel';

INSERT INTO DIM_STORE
WITH src AS (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
             FROM TABLE(GENERATOR(rowcount => 200))),
geo AS (
    SELECT rn,
        SPLIT_PART(
            'New York:Los Angeles:Chicago:Houston:Phoenix:Philadelphia:Seattle:'
            'Denver:Nashville:Miami:Dallas:Atlanta:Boston:San Francisco:Portland:'
            'Las Vegas:Minneapolis:Detroit:Kansas City:Salt Lake City:Orlando:'
            'Pittsburgh:Cincinnati:St. Louis:Tampa:Charlotte:Raleigh:Baltimore:'
            'Sacramento:Indianapolis:Austin:Jacksonville:Fort Worth:Columbus:San Diego:'
            'San Jose:Memphis:Louisville:Hartford:Richmond:Tucson:Fresno:Mesa:'
            'Omaha:Colorado Springs:Raleigh:Albuquerque:Long Beach:Virginia Beach:Oakland',
            ':', MOD(rn - 1, 50) + 1)  AS city,
        SPLIT_PART(
            'NY:CA:IL:TX:AZ:PA:WA:CO:TN:FL:TX:GA:MA:CA:OR:'
            'NV:MN:MI:MO:UT:FL:PA:OH:MO:FL:NC:NC:MD:CA:IN:'
            'TX:FL:TX:OH:CA:CA:TN:KY:CT:VA:AZ:CA:AZ:NE:CO:NC:NM:CA:VA:CA',
            ':', MOD(rn - 1, 50) + 1)  AS state,
        SPLIT_PART(
            'Northeast:West:Midwest:South:Southwest:Northeast:West:Mountain:South:Southeast:'
            'South:Southeast:Northeast:West:West:Southwest:Midwest:Midwest:Midwest:Mountain:'
            'Southeast:Northeast:Midwest:Midwest:Southeast:Southeast:Southeast:Northeast:West:Midwest:'
            'South:Southeast:South:Midwest:West:West:South:South:Northeast:Southeast:'
            'Southwest:West:Southwest:Midwest:Mountain:Southeast:Southwest:West:Southeast:West',
            ':', MOD(rn - 1, 50) + 1)  AS region,
        SPLIT_PART('Tier1:Tier1:Tier1:Tier2:Tier2:Tier2:Tier2:Tier2:Tier2:Tier2:'
                   'Tier2:Tier2:Tier1:Tier1:Tier2:Tier2:Tier2:Tier2:Tier2:Tier2:'
                   'Tier2:Tier3:Tier3:Tier3:Tier3:Tier3:Tier3:Tier3:Tier3:Tier3:'
                   'Tier2:Tier3:Tier2:Tier3:Tier2:Tier2:Tier3:Tier3:Tier3:Tier3:'
                   'Tier3:Tier3:Tier3:Tier3:Tier3:Tier3:Tier3:Tier3:Tier3:Tier3',
                   ':', MOD(rn - 1, 50) + 1)  AS tier
    FROM src
),
typed AS (
    SELECT *, CASE MOD(rn - 1, 4)
        WHEN 0 THEN 'Flagship'
        WHEN 1 THEN 'Standard'
        WHEN 2 THEN 'Outlet'
        ELSE        'Online'
    END AS store_type
    FROM geo
)
SELECT
    rn                                                             AS STORE_KEY,
    'STR' || LPAD(rn, 5, '0')                                    AS STORE_ID,
    store_type || ' - ' || city                                   AS STORE_NAME,
    city, state, region, store_type,
    store_type = 'Online'                                         AS IS_ONLINE,
    DATEADD('day', -(UNIFORM(90, 5000, RANDOM()))::INT,
            '2023-01-01'::DATE)                                    AS OPEN_DATE,
    CASE store_type
        WHEN 'Flagship' THEN UNIFORM(10000, 20000, RANDOM())::INT
        WHEN 'Standard' THEN UNIFORM(3000,  8000,  RANDOM())::INT
        WHEN 'Outlet'   THEN UNIFORM(1500,  4000,  RANDOM())::INT
        ELSE NULL
    END                                                            AS SQUARE_FOOTAGE,
    tier                                                           AS TIER
FROM typed;

-- =============================================================================
-- 6. DIM_EMPLOYEE  — 600 rows
-- =============================================================================
CREATE OR REPLACE TABLE DIM_EMPLOYEE (
    EMPLOYEE_KEY   NUMBER       NOT NULL  PRIMARY KEY,
    EMPLOYEE_ID    VARCHAR(10)  NOT NULL  UNIQUE,
    FIRST_NAME     VARCHAR(30)  NOT NULL,
    LAST_NAME      VARCHAR(30)  NOT NULL,
    JOB_TITLE      VARCHAR(40)  NOT NULL,
    DEPARTMENT     VARCHAR(30)  NOT NULL,
    STORE_KEY      NUMBER       NOT NULL,
    HIRE_DATE      DATE         NOT NULL,
    PERFORMANCE    VARCHAR(10)  NOT NULL,   -- Top Good Average Low
    IS_ACTIVE      BOOLEAN      NOT NULL  DEFAULT TRUE
) COMMENT = 'Employee dimension — 600 staff. PERFORMANCE column supports sales attribution analysis.';

INSERT INTO DIM_EMPLOYEE
WITH src AS (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
             FROM TABLE(GENERATOR(rowcount => 600)))
SELECT
    rn                                                               AS EMPLOYEE_KEY,
    'EMP' || LPAD(rn, 6, '0')                                      AS EMPLOYEE_ID,
    SPLIT_PART('James:Mary:John:Patricia:Robert:Jennifer:Michael:Linda:'
               'William:Barbara:David:Elizabeth:Richard:Susan:Joseph:'
               'Jessica:Thomas:Sarah:Charles:Karen:Christopher:Lisa:Daniel:'
               'Nancy:Matthew:Betty:Anthony:Sandra:Mark:Donna',
               ':', MOD(rn - 1, 30) + 1)                            AS FIRST_NAME,
    SPLIT_PART('Smith:Johnson:Williams:Brown:Jones:Garcia:Miller:Davis:'
               'Rodriguez:Martinez:Hernandez:Lopez:Gonzalez:Wilson:Anderson:'
               'Thomas:Taylor:Moore:Jackson:Martin:Lee:Thompson:White:'
               'Harris:Sanchez:Clark:Ramirez:Lewis:Robinson:Walker',
               ':', MOD(rn * 13 + 7, 30) + 1)                       AS LAST_NAME,
    SPLIT_PART('Sales Associate:Senior Sales Associate:Department Manager:'
               'Assistant Manager:Store Manager:Cashier:Team Lead:Visual Merchandiser',
               ':', MOD(rn - 1, 8) + 1)                             AS JOB_TITLE,
    SPLIT_PART('Sales:Sales:Customer Service:Operations:Management:Sales:Operations:Sales',
               ':', MOD(rn - 1, 8) + 1)                             AS DEPARTMENT,
    MOD(rn - 1, 200) + 1                                             AS STORE_KEY,
    DATEADD('day', -(UNIFORM(30, 3650, RANDOM()))::INT,
            '2023-01-01'::DATE)                                       AS HIRE_DATE,
    CASE MOD(rn, 10)
        WHEN 0 THEN 'Top'
        WHEN 1 THEN 'Top'
        WHEN 2 THEN 'Good'
        WHEN 3 THEN 'Good'
        WHEN 4 THEN 'Good'
        WHEN 5 THEN 'Average'
        WHEN 6 THEN 'Average'
        WHEN 7 THEN 'Average'
        WHEN 8 THEN 'Average'
        ELSE        'Low'
    END                                                               AS PERFORMANCE,
    IFF(MOD(rn, 30) = 0, FALSE, TRUE)                               AS IS_ACTIVE
FROM src;

-- =============================================================================
-- 7. DIM_PROMOTION  — 200 rows
--    Anti-correlated with demand: clearance in Q1, loyalty in Q2-Q3, scarcity in Q4
-- =============================================================================
CREATE OR REPLACE TABLE DIM_PROMOTION (
    PROMOTION_KEY    NUMBER        NOT NULL  PRIMARY KEY,
    PROMOTION_ID     VARCHAR(12)   NOT NULL  UNIQUE,
    PROMOTION_NAME   VARCHAR(70)   NOT NULL,
    PROMOTION_TYPE   VARCHAR(15)   NOT NULL,
    DISCOUNT_PERCENT NUMBER(5,2)   NOT NULL,
    TARGET_SEGMENT   VARCHAR(15)   NOT NULL,   -- All Platinum Gold Silver Bronze
    TARGET_CHANNEL   VARCHAR(15)   NOT NULL,   -- All Online Physical
    START_DATE       DATE          NOT NULL,
    END_DATE         DATE          NOT NULL,
    IS_ACTIVE        BOOLEAN       NOT NULL  DEFAULT TRUE
) COMMENT = 'Promotion dimension — 200 campaigns. Higher discount in slow seasons (Q1 clearance).';

INSERT INTO DIM_PROMOTION
WITH src AS (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
             FROM TABLE(GENERATOR(rowcount => 200))),
types AS (
    SELECT rn,
        CASE MOD(rn - 1, 5)
            WHEN 0 THEN 'Seasonal'
            WHEN 1 THEN 'Loyalty'
            WHEN 2 THEN 'Flash'
            WHEN 3 THEN 'Clearance'
            ELSE        'Bundle'
        END AS promo_type,
        -- Organic discount: Clearance highest, Bundle lowest
        ROUND(CASE MOD(rn - 1, 5)
            WHEN 0 THEN UNIFORM(10.0, 25.0, RANDOM())
            WHEN 1 THEN UNIFORM(5.0,  15.0, RANDOM())
            WHEN 2 THEN UNIFORM(20.0, 45.0, RANDOM())
            WHEN 3 THEN UNIFORM(30.0, 60.0, RANDOM())
            ELSE        UNIFORM(8.0,  18.0, RANDOM())
        END::FLOAT, 2)  AS disc_pct,
        SPLIT_PART('All:Platinum:Gold:Silver:Bronze:All:All:Platinum:Gold:All',
                   ':', MOD(rn, 10) + 1)  AS target_seg,
        SPLIT_PART('All:Online:Physical:All:Online:Physical:All:All:Online:Physical',
                   ':', MOD(rn, 10) + 1)  AS target_chan,
        DATEADD('day', (rn - 1) * 3, '2023-01-01'::DATE)  AS start_dt
    FROM src
)
SELECT
    rn                                                             AS PROMOTION_KEY,
    'PROMO' || LPAD(rn, 6, '0')                                  AS PROMOTION_ID,
    CONCAT(promo_type, ' #', rn, ' — ', target_seg, ' ', target_chan)
                                                                   AS PROMOTION_NAME,
    promo_type                                                     AS PROMOTION_TYPE,
    disc_pct                                                       AS DISCOUNT_PERCENT,
    target_seg                                                     AS TARGET_SEGMENT,
    target_chan                                                     AS TARGET_CHANNEL,
    start_dt                                                       AS START_DATE,
    DATEADD('day', UNIFORM(7, 45, RANDOM())::INT, start_dt)       AS END_DATE,
    IFF(rn <= 140, TRUE, FALSE)                                    AS IS_ACTIVE
FROM types;

-- =============================================================================
-- 8. FACT_SALES_ORGANIC  — 12,000,000 rows
--
--  DATE DISTRIBUTION (organic / seasonal):
--
--    Block  Season      Date Range              Rows        Avg/day
--    ─────  ──────────  ─────────────────────   ─────────   ──────
--    B1     Winter-23   Jan  1 – Mar 31 2023    1,200,000   13,333
--    B2     Spring-23   Apr  1 – Jun 30 2023    1,800,000   20,000
--    B3     Summer-23   Jul  1 – Sep 30 2023    2,300,000   25,556
--    B4     Fall/Hol23  Oct  1 – Dec 31 2023    3,500,000   38,889
--    B5     H1-2024     Jan  1 – Jun 30 2024    1,500,000   16,667
--    B6     H2-2024     Jul  1 – Dec 31 2024    1,700,000   18,889
--    ─────                                     ──────────
--    TOTAL                                     12,000,000
--
--  PRODUCT-SEASON AFFINITY (prod_rv → category key range):
--
--    Category        Q1     Q2     Q3     Q4    Key range
--    ─────────────   ────   ────   ────   ────  ─────────
--    Electronics      20%    22%    25%    40%    1– 200
--    Clothing         18%    28%    22%    18%  201– 400
--    Home & Living    14%    22%    18%    12%  401– 560
--    Sports           12%    16%    28%    12%  561– 700
--    Food & Bev       16%    12%    07%    18%  701– 800
--    (probabilities are share of transactions, not exact)
--
--  PROMOTION RATE (anti-correlated with peak demand):
--    Q1 (slow)    : 48%   ← clearance + loyalty drives
--    Q2 (growing) : 32%
--    Q3 (summer)  : 24%
--    Q4 (holiday) : 18%   ← demand is high; fewer promos needed
-- =============================================================================

CREATE OR REPLACE TABLE FACT_SALES_ORGANIC (
    SALE_KEY        NUMBER        NOT NULL  PRIMARY KEY,
    DATE_KEY        NUMBER(8)     NOT NULL,
    CUSTOMER_KEY    NUMBER        NOT NULL,
    PRODUCT_KEY     NUMBER        NOT NULL,
    STORE_KEY       NUMBER        NOT NULL,
    EMPLOYEE_KEY    NUMBER        NOT NULL,
    PROMOTION_KEY   NUMBER,
    CHANNEL_KEY     NUMBER        NOT NULL,
    TRANSACTION_ID  VARCHAR(20)   NOT NULL  UNIQUE,
    PAYMENT_METHOD  VARCHAR(15)   NOT NULL,
    QUANTITY        NUMBER(3)     NOT NULL,
    UNIT_PRICE      NUMBER(10,2)  NOT NULL,
    UNIT_COST       NUMBER(10,2)  NOT NULL,
    DISCOUNT_AMOUNT NUMBER(10,2)  NOT NULL  DEFAULT 0,
    GROSS_AMOUNT    NUMBER(10,2)  NOT NULL,
    NET_AMOUNT      NUMBER(10,2)  NOT NULL,
    TAX_AMOUNT      NUMBER(10,2)  NOT NULL,
    MARGIN_AMOUNT   NUMBER(10,2)  NOT NULL,
    IS_WEEKEND      BOOLEAN       NOT NULL,
    IS_HOLIDAY      BOOLEAN       NOT NULL,
    SEASON          VARCHAR(10)   NOT NULL
)
COMMENT = '12M organic sales transactions with realistic seasonal, channel and product-affinity distributions.'
CLUSTER BY (DATE_KEY, CUSTOMER_KEY);

-- ─────────────────────────────────────────────────────────────────────────────
-- Block helper macro: generates one seasonal block.
--   @start_date  First day of the date range
--   @days        Number of days in the range
--   @rows        Number of records to generate for this block
--   @blk         Block sequence number (for SALE_KEY offset)
--   @promo_rate  Fraction of transactions that have a promotion (0.0–1.0)
--   @prod weights p_elec p_clth p_home p_sprt   (cumulative probabilities)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO FACT_SALES_ORGANIC
WITH

-- ── B1: WINTER 2023  (Jan–Mar, slow season) ──────────────────────────────────
b1 AS (
    SELECT
        1                                                            AS blk,
        ROW_NUMBER() OVER (ORDER BY SEQ4())                         AS rn,
        DATEADD('day', UNIFORM(0, 89, RANDOM())::INT, '2023-01-01'::DATE)  AS sale_dt,
        -- Customer: Pareto — top 30% cust generate 60% of Q1 slow-season buys
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.60,
            UNIFORM(1, 3000, RANDOM())::INT,
            UNIFORM(3001, 10000, RANDOM())::INT)                    AS cust_k,
        -- Product affinity: Q1 = Food↑, Electronics↓
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.20 THEN UNIFORM(1,   200, RANDOM())::INT  -- Elec 20%
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.38 THEN UNIFORM(201, 400, RANDOM())::INT  -- Clth 18%
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.52 THEN UNIFORM(401, 560, RANDOM())::INT  -- Home 14%
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.64 THEN UNIFORM(561, 700, RANDOM())::INT  -- Sprt 12%
             ELSE                                                    UNIFORM(701, 800, RANDOM())::INT  -- Food 16%
        END                                                         AS prod_k,
        UNIFORM(1,  200, RANDOM())::INT                             AS store_k,
        UNIFORM(1,  600, RANDOM())::INT                             AS emp_k,
        -- Promo: 48% in Q1 (high discount / clearance season)
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.48, UNIFORM(1, 200, RANDOM())::INT, NULL)  AS promo_k,
        -- Channel: less online in Q1 (lower digital affinity off-peak)
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.55 THEN 1
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.80 THEN 2
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.90 THEN 3
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.96 THEN 4
             ELSE 5 END                                             AS channel_k,
        UNIFORM(0.0, 1.0, RANDOM())                                 AS rv_qty
    FROM TABLE(GENERATOR(rowcount => 1200000))
),

-- ── B2: SPRING 2023  (Apr–Jun, fashion & home season) ────────────────────────
b2 AS (
    SELECT
        2 AS blk,
        ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn,
        DATEADD('day', UNIFORM(0, 90, RANDOM())::INT, '2023-04-01'::DATE) AS sale_dt,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.55,
            UNIFORM(1, 3000, RANDOM())::INT, UNIFORM(3001, 10000, RANDOM())::INT) AS cust_k,
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.22 THEN UNIFORM(1,   200, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.50 THEN UNIFORM(201, 400, RANDOM())::INT  -- Clth 28%
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.72 THEN UNIFORM(401, 560, RANDOM())::INT  -- Home 22%
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.88 THEN UNIFORM(561, 700, RANDOM())::INT  -- Sprt 16%
             ELSE                                                    UNIFORM(701, 800, RANDOM())::INT
        END AS prod_k,
        UNIFORM(1, 200, RANDOM())::INT AS store_k,
        UNIFORM(1, 600, RANDOM())::INT AS emp_k,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.32, UNIFORM(1, 200, RANDOM())::INT, NULL) AS promo_k,
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.50 THEN 1
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.78 THEN 2
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.89 THEN 3
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.95 THEN 4
             ELSE 5 END AS channel_k,
        UNIFORM(0.0, 1.0, RANDOM()) AS rv_qty
    FROM TABLE(GENERATOR(rowcount => 1800000))
),

-- ── B3: SUMMER 2023  (Jul–Sep, sports & outdoor peak) ────────────────────────
b3 AS (
    SELECT
        3 AS blk,
        ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn,
        DATEADD('day', UNIFORM(0, 91, RANDOM())::INT, '2023-07-01'::DATE) AS sale_dt,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.55,
            UNIFORM(1, 3000, RANDOM())::INT, UNIFORM(3001, 10000, RANDOM())::INT) AS cust_k,
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.25 THEN UNIFORM(1,   200, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.47 THEN UNIFORM(201, 400, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.65 THEN UNIFORM(401, 560, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.93 THEN UNIFORM(561, 700, RANDOM())::INT  -- Sports 28%
             ELSE                                                    UNIFORM(701, 800, RANDOM())::INT
        END AS prod_k,
        UNIFORM(1, 200, RANDOM())::INT AS store_k,
        UNIFORM(1, 600, RANDOM())::INT AS emp_k,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.24, UNIFORM(1, 200, RANDOM())::INT, NULL) AS promo_k,
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.45 THEN 1
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.76 THEN 2
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.88 THEN 3
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.95 THEN 4
             ELSE 5 END AS channel_k,
        UNIFORM(0.0, 1.0, RANDOM()) AS rv_qty
    FROM TABLE(GENERATOR(rowcount => 2300000))
),

-- ── B4: HOLIDAY 2023  (Oct–Dec, peak season) ─────────────────────────────────
b4 AS (
    SELECT
        4 AS blk,
        ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn,
        DATEADD('day', UNIFORM(0, 91, RANDOM())::INT, '2023-10-01'::DATE) AS sale_dt,
        -- Q4: top 20% customers generate even more (gift buying + loyalty)
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.65,
            UNIFORM(1, 2000, RANDOM())::INT, UNIFORM(2001, 10000, RANDOM())::INT) AS cust_k,
        -- Q4: Electronics dominant (gifts)
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.40 THEN UNIFORM(1,   200, RANDOM())::INT  -- Elec 40%!
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.58 THEN UNIFORM(201, 400, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.70 THEN UNIFORM(401, 560, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.82 THEN UNIFORM(561, 700, RANDOM())::INT
             ELSE                                                    UNIFORM(701, 800, RANDOM())::INT
        END AS prod_k,
        UNIFORM(1, 200, RANDOM())::INT AS store_k,
        UNIFORM(1, 600, RANDOM())::INT AS emp_k,
        -- Q4: only 18% promo rate (demand is self-sufficient; Black Friday targeted)
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.18, UNIFORM(1, 200, RANDOM())::INT, NULL) AS promo_k,
        -- Q4: online surges (gift shipping)
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.40 THEN 1
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.75 THEN 2  -- Online 35% in Q4!
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.87 THEN 3
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.94 THEN 4
             ELSE 5 END AS channel_k,
        UNIFORM(0.0, 1.0, RANDOM()) AS rv_qty
    FROM TABLE(GENERATOR(rowcount => 3500000))
),

-- ── B5: H1 2024  (Jan–Jun, YoY growth) ───────────────────────────────────────
b5 AS (
    SELECT
        5 AS blk,
        ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn,
        DATEADD('day', UNIFORM(0, 181, RANDOM())::INT, '2024-01-01'::DATE) AS sale_dt,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.58,
            UNIFORM(1, 3000, RANDOM())::INT, UNIFORM(3001, 10000, RANDOM())::INT) AS cust_k,
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.24 THEN UNIFORM(1,   200, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.47 THEN UNIFORM(201, 400, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.67 THEN UNIFORM(401, 560, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.83 THEN UNIFORM(561, 700, RANDOM())::INT
             ELSE                                                    UNIFORM(701, 800, RANDOM())::INT
        END AS prod_k,
        UNIFORM(1, 200, RANDOM())::INT AS store_k,
        UNIFORM(1, 600, RANDOM())::INT AS emp_k,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.35, UNIFORM(1, 200, RANDOM())::INT, NULL) AS promo_k,
        -- 2024: Mobile + Online grow vs 2023
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.42 THEN 1
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.72 THEN 2
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.86 THEN 3  -- Mobile growing
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.94 THEN 4
             ELSE 5 END AS channel_k,
        UNIFORM(0.0, 1.0, RANDOM()) AS rv_qty
    FROM TABLE(GENERATOR(rowcount => 1500000))
),

-- ── B6: H2 2024  (Jul–Dec, summer + holiday YoY growth) ─────────────────────
b6 AS (
    SELECT
        6 AS blk,
        ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn,
        DATEADD('day', UNIFORM(0, 183, RANDOM())::INT, '2024-07-01'::DATE) AS sale_dt,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.60,
            UNIFORM(1, 3000, RANDOM())::INT, UNIFORM(3001, 10000, RANDOM())::INT) AS cust_k,
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.30 THEN UNIFORM(1,   200, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.50 THEN UNIFORM(201, 400, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.67 THEN UNIFORM(401, 560, RANDOM())::INT
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.85 THEN UNIFORM(561, 700, RANDOM())::INT
             ELSE                                                    UNIFORM(701, 800, RANDOM())::INT
        END AS prod_k,
        UNIFORM(1, 200, RANDOM())::INT AS store_k,
        UNIFORM(1, 600, RANDOM())::INT AS emp_k,
        IFF(UNIFORM(0.0, 1.0, RANDOM()) < 0.22, UNIFORM(1, 200, RANDOM())::INT, NULL) AS promo_k,
        CASE WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.38 THEN 1
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.72 THEN 2
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.88 THEN 3
             WHEN UNIFORM(0.0, 1.0, RANDOM()) < 0.95 THEN 4
             ELSE 5 END AS channel_k,
        UNIFORM(0.0, 1.0, RANDOM()) AS rv_qty
    FROM TABLE(GENERATOR(rowcount => 1700000))
),

-- ── Combine all seasonal blocks ───────────────────────────────────────────────
all_raw AS (
    SELECT * FROM b1
    UNION ALL SELECT * FROM b2
    UNION ALL SELECT * FROM b3
    UNION ALL SELECT * FROM b4
    UNION ALL SELECT * FROM b5
    UNION ALL SELECT * FROM b6
),

-- ── Derive organic quantity based on product category ─────────────────────────
enriched AS (
    SELECT
        r.*,
        p.UNIT_PRICE,
        p.UNIT_COST,
        p.CATEGORY,
        COALESCE(pr.DISCOUNT_PERCENT, 0.0) AS disc_pct,
        ch.BASE_AOV_MULTIPLIER              AS ch_multiplier,
        -- Weekend detection (day-of-week 0=Sun, 6=Sat)
        DAYOFWEEK(r.sale_dt) IN (0, 6)     AS is_weekend,
        -- Organic quantity by category:
        --   Electronics: almost always 1 (expensive tech items)
        --   Food: higher quantities (consumables, stock up)
        --   Weekend bonus: +30% quantity when IS_WEEKEND
        GREATEST(1, LEAST(
            CASE p.CATEGORY
                WHEN 'Electronics'    THEN 5
                WHEN 'Clothing'       THEN 10
                WHEN 'Home & Living'  THEN 8
                WHEN 'Sports'         THEN 7
                ELSE                       15   -- Food: up to 15 units
            END,
            ROUND(
                NORMAL(
                    CASE p.CATEGORY
                        WHEN 'Electronics'   THEN 1.2
                        WHEN 'Clothing'      THEN 2.6
                        WHEN 'Home & Living' THEN 2.1
                        WHEN 'Sports'        THEN 1.9
                        ELSE                      4.8  -- Food/Bev
                    END
                    * IFF(DAYOFWEEK(r.sale_dt) IN (0, 6), 1.30, 1.0),  -- weekend +30%
                    CASE p.CATEGORY
                        WHEN 'Electronics'   THEN 0.5
                        WHEN 'Clothing'      THEN 1.4
                        WHEN 'Home & Living' THEN 1.2
                        WHEN 'Sports'        THEN 1.1
                        ELSE                      2.2
                    END,
                    RANDOM()
                )
            )::INT
        ))  AS qty,
        -- Derive season name from month
        CASE MONTH(r.sale_dt)
            WHEN 12 THEN 'Winter' WHEN 1 THEN 'Winter' WHEN 2 THEN 'Winter'
            WHEN  3 THEN 'Spring' WHEN 4 THEN 'Spring' WHEN 5 THEN 'Spring'
            WHEN  6 THEN 'Summer' WHEN 7 THEN 'Summer' WHEN 8 THEN 'Summer'
            ELSE 'Fall'
        END  AS season_name
    FROM all_raw r
    JOIN DIM_PRODUCT  p  ON p.PRODUCT_KEY   = r.prod_k
    JOIN DIM_CHANNEL  ch ON ch.CHANNEL_KEY  = r.channel_k
    LEFT JOIN DIM_PROMOTION pr ON pr.PROMOTION_KEY = r.promo_k
),

-- ── Build global sale key and final financial columns ─────────────────────────
numbered AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY blk, rn) AS sale_key,
        *
    FROM enriched
)
SELECT
    sale_key                                                AS SALE_KEY,
    TO_NUMBER(TO_CHAR(sale_dt, 'YYYYMMDD'))                AS DATE_KEY,
    cust_k                                                  AS CUSTOMER_KEY,
    prod_k                                                  AS PRODUCT_KEY,
    store_k                                                 AS STORE_KEY,
    emp_k                                                   AS EMPLOYEE_KEY,
    promo_k                                                 AS PROMOTION_KEY,
    channel_k                                               AS CHANNEL_KEY,
    'TXN' || LPAD(sale_key, 10, '0')                       AS TRANSACTION_ID,
    -- Payment method varies by channel and season
    CASE MOD(sale_key, 5)
        WHEN 0 THEN 'CREDIT_CARD'
        WHEN 1 THEN 'DEBIT_CARD'
        WHEN 2 THEN 'CASH'
        WHEN 3 THEN 'DIGITAL_WALLET'
        ELSE        'BUY_NOW_PAY_LATER'
    END                                                     AS PAYMENT_METHOD,
    qty                                                     AS QUANTITY,
    ROUND(UNIT_PRICE * ch_multiplier, 2)                   AS UNIT_PRICE,
    UNIT_COST,
    ROUND(UNIT_PRICE * ch_multiplier * qty * disc_pct / 100.0, 2) AS DISCOUNT_AMOUNT,
    ROUND(UNIT_PRICE * ch_multiplier * qty, 2)             AS GROSS_AMOUNT,
    ROUND(UNIT_PRICE * ch_multiplier * qty * (1.0 - disc_pct / 100.0), 2) AS NET_AMOUNT,
    ROUND(UNIT_PRICE * ch_multiplier * qty * (1.0 - disc_pct / 100.0) * 0.08, 2) AS TAX_AMOUNT,
    ROUND((UNIT_PRICE * ch_multiplier - UNIT_COST) * qty * (1.0 - disc_pct / 100.0), 2) AS MARGIN_AMOUNT,
    is_weekend                                              AS IS_WEEKEND,
    sale_dt IN (
        '2023-01-01'::DATE, '2023-07-04'::DATE, '2023-11-23'::DATE,
        '2023-12-25'::DATE, '2024-01-01'::DATE, '2024-07-04'::DATE,
        '2024-11-28'::DATE, '2024-12-25'::DATE
    )                                                       AS IS_HOLIDAY,
    season_name                                             AS SEASON
FROM numbered;

-- =============================================================================
-- 9. VERIFICATION
-- =============================================================================

-- Row counts
SELECT 'DIM_DATE'           AS tbl, COUNT(*) AS row_count FROM DIM_DATE
UNION ALL SELECT 'DIM_CHANNEL',    COUNT(*) FROM DIM_CHANNEL
UNION ALL SELECT 'DIM_CUSTOMER',   COUNT(*) FROM DIM_CUSTOMER
UNION ALL SELECT 'DIM_PRODUCT',    COUNT(*) FROM DIM_PRODUCT
UNION ALL SELECT 'DIM_STORE',      COUNT(*) FROM DIM_STORE
UNION ALL SELECT 'DIM_EMPLOYEE',   COUNT(*) FROM DIM_EMPLOYEE
UNION ALL SELECT 'DIM_PROMOTION',  COUNT(*) FROM DIM_PROMOTION
UNION ALL SELECT 'FACT_SALES_ORGANIC', COUNT(*) FROM FACT_SALES_ORGANIC
ORDER BY 1;

-- Date distribution: confirms organic seasonal spread across 730 days
SELECT
    d.YEAR,
    d.QUARTER,
    d.SEASON,
    COUNT(f.SALE_KEY)                             AS num_transactions,
    ROUND(COUNT(f.SALE_KEY) / 91.0, 0)           AS avg_per_day,
    ROUND(SUM(f.NET_AMOUNT), 0)                   AS net_revenue,
    ROUND(AVG(f.NET_AMOUNT), 2)                   AS avg_order_value
FROM FACT_SALES_ORGANIC f
JOIN DIM_DATE d ON d.DATE_KEY = f.DATE_KEY
GROUP BY 1, 2, 3
ORDER BY 1, 2;

-- Channel distribution
SELECT
    ch.CHANNEL_NAME,
    COUNT(*)                             AS transactions,
    ROUND(COUNT(*) * 100.0 /
          SUM(COUNT(*)) OVER (), 2)      AS pct_of_total,
    ROUND(AVG(f.NET_AMOUNT), 2)         AS avg_order_value
FROM FACT_SALES_ORGANIC f
JOIN DIM_CHANNEL ch ON ch.CHANNEL_KEY = f.CHANNEL_KEY
GROUP BY 1
ORDER BY 2 DESC;

-- Product category volume by season (confirms organic affinity)
SELECT
    p.CATEGORY,
    f.SEASON,
    COUNT(*)                    AS transactions,
    SUM(f.QUANTITY)             AS units_sold,
    ROUND(SUM(f.NET_AMOUNT), 0) AS net_revenue
FROM FACT_SALES_ORGANIC f
JOIN DIM_PRODUCT p ON p.PRODUCT_KEY = f.PRODUCT_KEY
GROUP BY 1, 2
ORDER BY 1, 2;

-- Customer segment Pareto check (Platinum+Gold should drive >60% of revenue)
SELECT
    c.CUSTOMER_SEGMENT,
    COUNT(DISTINCT f.CUSTOMER_KEY)              AS unique_customers,
    COUNT(*)                                    AS transactions,
    ROUND(SUM(f.NET_AMOUNT), 0)                AS net_revenue,
    ROUND(SUM(f.NET_AMOUNT) * 100.0 /
          SUM(SUM(f.NET_AMOUNT)) OVER (), 2)   AS revenue_share_pct
FROM FACT_SALES_ORGANIC f
JOIN DIM_CUSTOMER c ON c.CUSTOMER_KEY = f.CUSTOMER_KEY
GROUP BY 1
ORDER BY net_revenue DESC;

-- FK integrity: all keys must resolve (all return 0)
SELECT 'orphan_dates'      AS check_name, COUNT(*) AS orphans FROM FACT_SALES_ORGANIC f WHERE NOT EXISTS (SELECT 1 FROM DIM_DATE d      WHERE d.DATE_KEY      = f.DATE_KEY)
UNION ALL SELECT 'orphan_customers',     COUNT(*) FROM FACT_SALES_ORGANIC f WHERE NOT EXISTS (SELECT 1 FROM DIM_CUSTOMER c  WHERE c.CUSTOMER_KEY  = f.CUSTOMER_KEY)
UNION ALL SELECT 'orphan_products',      COUNT(*) FROM FACT_SALES_ORGANIC f WHERE NOT EXISTS (SELECT 1 FROM DIM_PRODUCT p   WHERE p.PRODUCT_KEY   = f.PRODUCT_KEY)
UNION ALL SELECT 'orphan_stores',        COUNT(*) FROM FACT_SALES_ORGANIC f WHERE NOT EXISTS (SELECT 1 FROM DIM_STORE s     WHERE s.STORE_KEY     = f.STORE_KEY)
UNION ALL SELECT 'orphan_employees',     COUNT(*) FROM FACT_SALES_ORGANIC f WHERE NOT EXISTS (SELECT 1 FROM DIM_EMPLOYEE e  WHERE e.EMPLOYEE_KEY  = f.EMPLOYEE_KEY)
UNION ALL SELECT 'orphan_channels',      COUNT(*) FROM FACT_SALES_ORGANIC f WHERE NOT EXISTS (SELECT 1 FROM DIM_CHANNEL ch  WHERE ch.CHANNEL_KEY  = f.CHANNEL_KEY);

-- =============================================================================
-- 10. Update config.yaml (web UI)
-- =============================================================================
-- After running this script, point the Profiler at:
--   database: SAMPLE_DW
--   schema:   ORGANIC
-- then profile FACT_SALES_ORGANIC (12M rows).
--
-- Interesting clustering experiments:
--   Features: QUANTITY, UNIT_PRICE, NET_AMOUNT, MARGIN_AMOUNT
--             IS_WEEKEND, CHANNEL_KEY, CUSTOMER_KEY
--   Expected clusters: ~4-6 meaningful behavioural groups
-- =============================================================================
