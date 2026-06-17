-- =============================================================================
-- setup_geolocation.sql
--
-- Adds a worldwide geolocation dimension and a Gold-layer fact table:
--
--   DIM_LOCATION       80 locations · 60 % Americas · 25 % Asia · 15 % Other
--   GOLD_FACT_ORDERS   Gold-layer CTAS from FACT_SALES_ORGANIC enriched with
--                      a deterministic but pseudo-random LOCATION_KEY
--
-- Geographic weight in GOLD_FACT_ORDERS
-- ──────────────────────────────────────
--   60 % Americas  (keys  1-48)
--   25 % Asia      (keys 49-68)
--   15 % Europe / Oceania / Other (keys 69-80)
--
-- Pre-requisites
-- ──────────────
--   • FACT_SALES_ORGANIC must exist (run setup_organic_sales.sql first)
--   • If you have already run migrate_date_key_to_date.sql, DATE_KEY in
--     FACT_SALES_ORGANIC is already a DATE; replace the TO_DATE(...) call
--     in the GOLD_FACT_ORDERS CTAS with just:   DATE_KEY AS ORDER_DATE
-- =============================================================================

USE DATABASE SAMPLE_DW;
USE SCHEMA   ORGANIC;    -- change if your tables live in a different schema


-- =============================================================================
-- 1. DIM_LOCATION  — 80 worldwide city-level locations
-- =============================================================================

CREATE OR REPLACE TABLE DIM_LOCATION (
    LOCATION_KEY    NUMBER       NOT NULL  PRIMARY KEY,
    LOCATION_ID     VARCHAR(10)  NOT NULL  UNIQUE,
    CITY            VARCHAR(60)  NOT NULL,
    STATE_PROVINCE  VARCHAR(60),
    COUNTRY         VARCHAR(60)  NOT NULL,
    COUNTRY_CODE    VARCHAR(2)   NOT NULL,   -- ISO 3166-1 alpha-2
    CONTINENT       VARCHAR(20)  NOT NULL,
    REGION          VARCHAR(40)  NOT NULL,
    LATITUDE        NUMBER(9,5)  NOT NULL,
    LONGITUDE       NUMBER(9,5)  NOT NULL,
    TIMEZONE        VARCHAR(50)  NOT NULL,
    IS_MAJOR_CITY   BOOLEAN      NOT NULL
)
COMMENT = 'Worldwide city-level geolocation dimension: 60% Americas, 25% Asia, 15% Europe/Other.';

INSERT INTO DIM_LOCATION VALUES
-- ── AMERICAS (keys 1-48) ─────────────────────────────────────────────────────
-- United States (1-20)
( 1,'LOC001','New York City',    'NY','United States','US','North America','USA Northeast',      40.71274, -74.00597,'America/New_York',    TRUE),
( 2,'LOC002','Los Angeles',      'CA','United States','US','North America','USA West Coast',     34.05223,-118.24368,'America/Los_Angeles',  TRUE),
( 3,'LOC003','Chicago',          'IL','United States','US','North America','USA Midwest',        41.85003, -87.65005,'America/Chicago',      TRUE),
( 4,'LOC004','Houston',          'TX','United States','US','North America','USA South',          29.76328, -95.36327,'America/Chicago',      TRUE),
( 5,'LOC005','Miami',            'FL','United States','US','North America','USA Southeast',      25.77427, -80.19366,'America/New_York',     TRUE),
( 6,'LOC006','Seattle',          'WA','United States','US','North America','USA West Coast',     47.60621,-122.33207,'America/Los_Angeles',  TRUE),
( 7,'LOC007','Denver',           'CO','United States','US','North America','USA Mountain West',  39.73915,-104.98470,'America/Denver',       TRUE),
( 8,'LOC008','Dallas',           'TX','United States','US','North America','USA South',          32.78306, -96.80667,'America/Chicago',      TRUE),
( 9,'LOC009','Phoenix',          'AZ','United States','US','North America','USA Southwest',      33.44838,-112.07404,'America/Phoenix',      TRUE),
(10,'LOC010','Atlanta',          'GA','United States','US','North America','USA Southeast',      33.74900, -84.38798,'America/New_York',     TRUE),
(11,'LOC011','San Francisco',    'CA','United States','US','North America','USA West Coast',     37.77493,-122.41942,'America/Los_Angeles',  TRUE),
(12,'LOC012','Boston',           'MA','United States','US','North America','USA Northeast',      42.35843, -71.05977,'America/New_York',     TRUE),
(13,'LOC013','Washington DC',    'DC','United States','US','North America','USA Northeast',      38.89511, -77.03637,'America/New_York',     TRUE),
(14,'LOC014','Las Vegas',        'NV','United States','US','North America','USA Southwest',      36.17497,-115.13722,'America/Los_Angeles',  FALSE),
(15,'LOC015','Austin',           'TX','United States','US','North America','USA South',          30.26715, -97.74306,'America/Chicago',      FALSE),
(16,'LOC016','Nashville',        'TN','United States','US','North America','USA Southeast',      36.16589, -86.78444,'America/Chicago',      FALSE),
(17,'LOC017','Minneapolis',      'MN','United States','US','North America','USA Midwest',        44.97997, -93.26384,'America/Chicago',      FALSE),
(18,'LOC018','San Diego',        'CA','United States','US','North America','USA West Coast',     32.71571,-117.15726,'America/Los_Angeles',  FALSE),
(19,'LOC019','Portland',         'OR','United States','US','North America','USA West Coast',     45.52345,-122.67621,'America/Los_Angeles',  FALSE),
(20,'LOC020','New Orleans',      'LA','United States','US','North America','USA South',          29.95465, -90.07507,'America/Chicago',      FALSE),
-- Canada (21-25)
(21,'LOC021','Toronto',          'ON','Canada',        'CA','North America','Canada',            43.70011, -79.41630,'America/Toronto',      TRUE),
(22,'LOC022','Vancouver',        'BC','Canada',        'CA','North America','Canada',            49.24966,-123.11934,'America/Vancouver',    TRUE),
(23,'LOC023','Montreal',         'QC','Canada',        'CA','North America','Canada',            45.50884, -73.58781,'America/Toronto',      TRUE),
(24,'LOC024','Calgary',          'AB','Canada',        'CA','North America','Canada',            51.04532,-114.05828,'America/Edmonton',     FALSE),
(25,'LOC025','Ottawa',           'ON','Canada',        'CA','North America','Canada',            45.42153, -75.69719,'America/Toronto',      FALSE),
-- Mexico (26-29)
(26,'LOC026','Mexico City',      NULL,'Mexico',        'MX','North America','Mexico & C. America',19.42847, -99.12766,'America/Mexico_City', TRUE),
(27,'LOC027','Guadalajara',      NULL,'Mexico',        'MX','North America','Mexico & C. America',20.66682,-103.39182,'America/Mexico_City', FALSE),
(28,'LOC028','Monterrey',        NULL,'Mexico',        'MX','North America','Mexico & C. America',25.67507,-100.31847,'America/Monterrey',   FALSE),
(29,'LOC029','Cancun',           NULL,'Mexico',        'MX','North America','Mexico & C. America',21.17429, -86.84656,'America/Cancun',      FALSE),
-- Brazil (30-34)
(30,'LOC030','Sao Paulo',        NULL,'Brazil',        'BR','South America','Brazil',           -23.54750, -46.63611,'America/Sao_Paulo',   TRUE),
(31,'LOC031','Rio de Janeiro',   NULL,'Brazil',        'BR','South America','Brazil',           -22.90278, -43.17167,'America/Sao_Paulo',   TRUE),
(32,'LOC032','Brasilia',         NULL,'Brazil',        'BR','South America','Brazil',           -15.77972, -47.92972,'America/Sao_Paulo',   TRUE),
(33,'LOC033','Belo Horizonte',   NULL,'Brazil',        'BR','South America','Brazil',           -19.91667, -43.93417,'America/Sao_Paulo',   FALSE),
(34,'LOC034','Fortaleza',        NULL,'Brazil',        'BR','South America','Brazil',            -3.71722, -38.54306,'America/Fortaleza',   FALSE),
-- Colombia (35-36)
(35,'LOC035','Bogota',           NULL,'Colombia',      'CO','South America','Andean Region',      4.71099, -74.07209,'America/Bogota',       TRUE),
(36,'LOC036','Medellin',         NULL,'Colombia',      'CO','South America','Andean Region',      6.25184, -75.56359,'America/Bogota',       FALSE),
-- Argentina (37-39)
(37,'LOC037','Buenos Aires',     NULL,'Argentina',     'AR','South America','Southern Cone',    -34.61315, -58.37723,'America/Argentina/Buenos_Aires',TRUE),
(38,'LOC038','Cordoba',          NULL,'Argentina',     'AR','South America','Southern Cone',    -31.42135, -64.18105,'America/Argentina/Cordoba',FALSE),
(39,'LOC039','Mendoza',          NULL,'Argentina',     'AR','South America','Southern Cone',    -32.89084, -68.82717,'America/Argentina/Mendoza',FALSE),
-- Chile (40-41)
(40,'LOC040','Santiago',         NULL,'Chile',         'CL','South America','Southern Cone',    -33.45694, -70.64827,'America/Santiago',    TRUE),
(41,'LOC041','Valparaiso',       NULL,'Chile',         'CL','South America','Southern Cone',    -33.04752, -71.61940,'America/Santiago',    FALSE),
-- Peru (42)
(42,'LOC042','Lima',             NULL,'Peru',          'PE','South America','Andean Region',    -12.04318, -77.02824,'America/Lima',        TRUE),
-- Ecuador (43)
(43,'LOC043','Quito',            NULL,'Ecuador',       'EC','South America','Andean Region',     -0.22985, -78.52495,'America/Guayaquil',   TRUE),
-- Venezuela (44)
(44,'LOC044','Caracas',          NULL,'Venezuela',     'VE','South America','Andean Region',    10.48801, -66.87919,'America/Caracas',     TRUE),
-- Uruguay (45)
(45,'LOC045','Montevideo',       NULL,'Uruguay',       'UY','South America','Southern Cone',    -34.90328, -56.18816,'America/Montevideo',  TRUE),
-- Panama (46)
(46,'LOC046','Panama City',      NULL,'Panama',        'PA','North America','Mexico & C. America', 8.99350, -79.51973,'America/Panama',     TRUE),
-- Jamaica (47)
(47,'LOC047','Kingston',         NULL,'Jamaica',       'JM','North America','Caribbean',        17.99702, -76.79358,'America/Jamaica',     TRUE),
-- Dominican Republic (48)
(48,'LOC048','Santo Domingo',    NULL,'Dominican Republic','DO','North America','Caribbean',    18.47378, -69.93112,'America/Santo_Domingo',TRUE),

-- ── ASIA (keys 49-68) ────────────────────────────────────────────────────────
-- Japan (49-51)
(49,'LOC049','Tokyo',            NULL,'Japan',         'JP','Asia',         'Japan',            35.68950, 139.69171,'Asia/Tokyo',          TRUE),
(50,'LOC050','Osaka',            NULL,'Japan',         'JP','Asia',         'Japan',            34.69374, 135.50218,'Asia/Tokyo',          TRUE),
(51,'LOC051','Nagoya',           NULL,'Japan',         'JP','Asia',         'Japan',            35.18147, 136.90641,'Asia/Tokyo',          FALSE),
-- China (52-55)
(52,'LOC052','Shanghai',         NULL,'China',         'CN','Asia',         'China',            31.22222, 121.45806,'Asia/Shanghai',       TRUE),
(53,'LOC053','Beijing',          NULL,'China',         'CN','Asia',         'China',            39.92889, 116.38833,'Asia/Shanghai',       TRUE),
(54,'LOC054','Guangzhou',        NULL,'China',         'CN','Asia',         'China',            23.11667, 113.25000,'Asia/Shanghai',       TRUE),
(55,'LOC055','Shenzhen',         NULL,'China',         'CN','Asia',         'China',            22.53280, 114.11920,'Asia/Shanghai',       FALSE),
-- South Korea (56-57)
(56,'LOC056','Seoul',            NULL,'South Korea',   'KR','Asia',         'Korean Peninsula', 37.56826, 126.97783,'Asia/Seoul',          TRUE),
(57,'LOC057','Busan',            NULL,'South Korea',   'KR','Asia',         'Korean Peninsula', 35.10280, 129.04028,'Asia/Seoul',          FALSE),
-- India (58-61)
(58,'LOC058','Mumbai',           NULL,'India',         'IN','Asia',         'South Asia',       19.07283,  72.88261,'Asia/Kolkata',        TRUE),
(59,'LOC059','New Delhi',        NULL,'India',         'IN','Asia',         'South Asia',       28.65195,  77.23149,'Asia/Kolkata',        TRUE),
(60,'LOC060','Bangalore',        NULL,'India',         'IN','Asia',         'South Asia',       12.97194,  77.59369,'Asia/Kolkata',        TRUE),
(61,'LOC061','Chennai',          NULL,'India',         'IN','Asia',         'South Asia',       13.08784,  80.27847,'Asia/Kolkata',        FALSE),
-- Southeast Asia (62-65)
(62,'LOC062','Singapore',        NULL,'Singapore',     'SG','Asia',         'Southeast Asia',    1.28967, 103.85007,'Asia/Singapore',      TRUE),
(63,'LOC063','Bangkok',          NULL,'Thailand',      'TH','Asia',         'Southeast Asia',   13.75398, 100.50144,'Asia/Bangkok',        TRUE),
(64,'LOC064','Ho Chi Minh City', NULL,'Vietnam',       'VN','Asia',         'Southeast Asia',   10.82302, 106.62965,'Asia/Ho_Chi_Minh',    TRUE),
(65,'LOC065','Jakarta',          NULL,'Indonesia',     'ID','Asia',         'Southeast Asia',   -6.21462, 106.84513,'Asia/Jakarta',        TRUE),
-- Other Asia (66-68)
(66,'LOC066','Manila',           NULL,'Philippines',   'PH','Asia',         'Southeast Asia',   14.59950, 120.98422,'Asia/Manila',         TRUE),
(67,'LOC067','Kuala Lumpur',     NULL,'Malaysia',      'MY','Asia',         'Southeast Asia',    3.14412, 101.68653,'Asia/Kuala_Lumpur',   TRUE),
(68,'LOC068','Taipei',           NULL,'Taiwan',        'TW','Asia',         'East Asia',        25.04776, 121.53185,'Asia/Taipei',         TRUE),

-- ── EUROPE / OCEANIA / OTHER (keys 69-80) ────────────────────────────────────
-- Europe (69-76)
(69,'LOC069','London',           NULL,'United Kingdom','GB','Europe',       'Western Europe',   51.50853,  -0.12574,'Europe/London',       TRUE),
(70,'LOC070','Paris',            NULL,'France',        'FR','Europe',       'Western Europe',   48.85341,   2.34880,'Europe/Paris',        TRUE),
(71,'LOC071','Madrid',           NULL,'Spain',         'ES','Europe',       'Western Europe',   40.41650,  -3.70256,'Europe/Madrid',       TRUE),
(72,'LOC072','Berlin',           NULL,'Germany',       'DE','Europe',       'Western Europe',   52.52437,  13.41053,'Europe/Berlin',       TRUE),
(73,'LOC073','Amsterdam',        NULL,'Netherlands',   'NL','Europe',       'Western Europe',   52.37403,   4.88969,'Europe/Amsterdam',    FALSE),
(74,'LOC074','Rome',             NULL,'Italy',         'IT','Europe',       'Western Europe',   41.89474,  12.48220,'Europe/Rome',         TRUE),
(75,'LOC075','Zurich',           NULL,'Switzerland',   'CH','Europe',       'Western Europe',   47.37689,   8.54169,'Europe/Zurich',       FALSE),
(76,'LOC076','Vienna',           NULL,'Austria',       'AT','Europe',       'Western Europe',   48.20849,  16.37208,'Europe/Vienna',       FALSE),
-- Oceania / Middle East / Africa (77-80)
(77,'LOC077','Sydney',           NULL,'Australia',     'AU','Oceania',      'Oceania',         -33.86785, 151.20732,'Australia/Sydney',    TRUE),
(78,'LOC078','Dubai',            NULL,'United Arab Emirates','AE','Asia',   'Middle East',      25.07725,  55.30927,'Asia/Dubai',          TRUE),
(79,'LOC079','Johannesburg',     NULL,'South Africa',  'ZA','Africa',       'Sub-Saharan Africa',-26.20227,28.04363,'Africa/Johannesburg', TRUE),
(80,'LOC080','Cairo',            NULL,'Egypt',         'EG','Africa',       'North Africa',     30.06263,  31.24967,'Africa/Cairo',        TRUE);


-- =============================================================================
-- 2. GOLD_FACT_ORDERS  — Gold layer enriched with geolocation
--
-- Built as CTAS from FACT_SALES_ORGANIC (12 000 000 rows).
-- LOCATION_KEY is assigned deterministically via HASH(TRANSACTION_ID) with
-- weights:  60 % Americas · 25 % Asia · 15 % Europe/Other
--
-- NOTE: If DATE_KEY in FACT_SALES_ORGANIC is still NUMBER(8) (i.e. you have NOT
-- run migrate_date_key_to_date.sql), the TO_DATE(...) call below converts it.
-- If DATE_KEY is already a DATE type, replace that expression with:
--     DATE_KEY  AS ORDER_DATE
-- =============================================================================

CREATE OR REPLACE TABLE GOLD_FACT_ORDERS
COMMENT = 'Gold-layer: 12M orders from FACT_SALES_ORGANIC enriched with global geolocation (DIM_LOCATION).'
CLUSTER BY (ORDER_DATE, LOCATION_KEY)
AS
SELECT
    SALE_KEY                                                         AS ORDER_KEY,
    -- ── date: handles both NUMBER(8) pre-migration and DATE post-migration ──
    TRY_TO_DATE(
        IFF(DATE_KEY < 100000000,
            LPAD(DATE_KEY::VARCHAR, 8, '0'),
            TO_CHAR(DATE_KEY::DATE, 'YYYYMMDD')
        ), 'YYYYMMDD'
    )                                                                AS ORDER_DATE,
    -- ── location weight: hash the transaction ID for determinism ────────────
    CASE
        WHEN MOD(ABS(HASH(TRANSACTION_ID)),      20) < 12   -- 60 % Americas
            THEN MOD(ABS(HASH(TRANSACTION_ID || 'A')), 48) + 1
        WHEN MOD(ABS(HASH(TRANSACTION_ID)),      20) < 17   -- 25 % Asia
            THEN MOD(ABS(HASH(TRANSACTION_ID || 'B')), 20) + 49
        ELSE                                                 -- 15 % Other
             MOD(ABS(HASH(TRANSACTION_ID || 'C')), 12) + 69
    END                                                              AS LOCATION_KEY,
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


-- =============================================================================
-- 3. Verification
-- =============================================================================

-- Row counts
SELECT 'DIM_LOCATION'    AS table_name, COUNT(*) AS row_count FROM DIM_LOCATION
UNION ALL
SELECT 'GOLD_FACT_ORDERS',               COUNT(*) FROM GOLD_FACT_ORDERS;

-- Location count by continent
SELECT CONTINENT, COUNT(*) AS num_locations
FROM DIM_LOCATION
GROUP BY CONTINENT
ORDER BY num_locations DESC;

-- GOLD_FACT_ORDERS: geographic distribution (must be ~60/25/15 %)
SELECT
    l.CONTINENT,
    COUNT(*)               AS order_count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct
FROM GOLD_FACT_ORDERS f
JOIN DIM_LOCATION l ON l.LOCATION_KEY = f.LOCATION_KEY
GROUP BY l.CONTINENT
ORDER BY order_count DESC;

-- Orphan check (must return 0)
SELECT COUNT(*) AS orphan_orders
FROM GOLD_FACT_ORDERS
WHERE LOCATION_KEY NOT BETWEEN 1 AND 80;

-- Top 10 cities by revenue
SELECT
    l.CITY,
    l.COUNTRY,
    l.CONTINENT,
    COUNT(*)                AS order_count,
    ROUND(SUM(f.NET_AMOUNT)/1e6, 2) AS net_revenue_m
FROM GOLD_FACT_ORDERS f
JOIN DIM_LOCATION l ON l.LOCATION_KEY = f.LOCATION_KEY
GROUP BY l.CITY, l.COUNTRY, l.CONTINENT
ORDER BY net_revenue_m DESC
LIMIT 10;

-- Date range
SELECT MIN(ORDER_DATE) AS min_date, MAX(ORDER_DATE) AS max_date
FROM GOLD_FACT_ORDERS;
