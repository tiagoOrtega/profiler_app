-- =============================================================================
-- Databricks / Apache Spark SQL — Retail Sales Star Schema
-- =============================================================================
-- Tables       Rows
-- ----------   ------
-- DIM_DATE       730   (2023-01-01 → 2024-12-31)
-- DIM_CUSTOMER   500
-- DIM_PRODUCT    200   (5 categories)
-- DIM_STORE       50   (10 US cities)
-- DIM_EMPLOYEE   100
-- DIM_PROMOTION   30
-- FACT_SALES   10 000
-- =============================================================================
-- ► Edit the catalog and schema variables below before running
-- =============================================================================

-- Set your target catalog and schema
DECLARE OR REPLACE VARIABLE v_catalog STRING DEFAULT 'main';
DECLARE OR REPLACE VARIABLE v_schema  STRING DEFAULT 'retail';

USE CATALOG main;        -- ← change if needed
CREATE SCHEMA IF NOT EXISTS retail COMMENT 'Retail sales star schema sample';
USE SCHEMA retail;

-- =============================================================================
-- 1. DIM_DATE
-- =============================================================================
CREATE OR REPLACE TABLE DIM_DATE (
    DATE_KEY     INT     NOT NULL,
    FULL_DATE    DATE    NOT NULL,
    DAY_OF_WEEK  INT     NOT NULL,
    DAY_NAME     STRING  NOT NULL,
    DAY_OF_MONTH INT     NOT NULL,
    DAY_OF_YEAR  INT     NOT NULL,
    WEEK_OF_YEAR INT     NOT NULL,
    MONTH_NUM    INT     NOT NULL,
    MONTH_NAME   STRING  NOT NULL,
    QUARTER      INT     NOT NULL,
    YEAR         INT     NOT NULL,
    IS_WEEKEND   BOOLEAN NOT NULL,
    IS_HOLIDAY   BOOLEAN NOT NULL
)
COMMENT 'Date dimension — 730 rows covering 2023-01-01 to 2024-12-31';

INSERT INTO DIM_DATE
SELECT
    CAST(DATE_FORMAT(d, 'yyyyMMdd') AS INT)                            AS DATE_KEY,
    d                                                                   AS FULL_DATE,
    DAYOFWEEK(d) - 1                                                   AS DAY_OF_WEEK,
    DATE_FORMAT(d, 'EEEE')                                             AS DAY_NAME,
    DAY(d)                                                             AS DAY_OF_MONTH,
    DAYOFYEAR(d)                                                       AS DAY_OF_YEAR,
    WEEKOFYEAR(d)                                                      AS WEEK_OF_YEAR,
    MONTH(d)                                                           AS MONTH_NUM,
    DATE_FORMAT(d, 'MMMM')                                            AS MONTH_NAME,
    QUARTER(d)                                                         AS QUARTER,
    YEAR(d)                                                            AS YEAR,
    DAYOFWEEK(d) IN (1, 7)                                            AS IS_WEEKEND,
    d IN (DATE'2023-01-01',DATE'2023-07-04',DATE'2023-11-23',
          DATE'2023-12-25',DATE'2024-01-01',DATE'2024-07-04',
          DATE'2024-11-28',DATE'2024-12-25')                           AS IS_HOLIDAY
FROM (
    SELECT DATE_ADD(DATE'2023-01-01', pos) AS d
    FROM (SELECT EXPLODE(SEQUENCE(0, 729)) AS pos)
);

-- =============================================================================
-- 2. DIM_CUSTOMER
-- =============================================================================
CREATE OR REPLACE TABLE DIM_CUSTOMER (
    CUSTOMER_KEY      INT     NOT NULL,
    CUSTOMER_ID       STRING  NOT NULL,
    FIRST_NAME        STRING  NOT NULL,
    LAST_NAME         STRING  NOT NULL,
    EMAIL             STRING  NOT NULL,
    GENDER            STRING  NOT NULL,
    BIRTH_DATE        DATE,
    CITY              STRING,
    STATE             STRING,
    COUNTRY           STRING  DEFAULT 'US',
    CUSTOMER_SEGMENT  STRING  NOT NULL,
    REGISTRATION_DATE DATE    NOT NULL
)
COMMENT 'Customer master dimension — 500 rows. ~5% null BIRTH_DATE (data quality test)';

INSERT INTO DIM_CUSTOMER
WITH src AS (SELECT pos + 1 AS rn FROM (SELECT EXPLODE(SEQUENCE(0, 499)) AS pos)),
names AS (
  SELECT rn,
    SPLIT('James:Mary:John:Patricia:Robert:Jennifer:Michael:Linda:William:Barbara:David:Elizabeth:Richard:Susan:Joseph:Jessica:Thomas:Sarah:Charles:Karen', ':')[MOD(rn-1,20)] AS fn,
    SPLIT('Smith:Johnson:Williams:Brown:Jones:Garcia:Miller:Davis:Rodriguez:Martinez:Hernandez:Lopez:Gonzalez:Wilson:Anderson:Thomas:Taylor:Moore:Jackson:Martin:Lee:Thompson:White:Harris:Sanchez:Clark', ':')[MOD(rn*7+2,26)] AS ln,
    SPLIT('New York:Los Angeles:Chicago:Houston:Phoenix:Philadelphia:Seattle:Denver:Nashville:Miami', ':')[MOD(rn*3,10)] AS city,
    SPLIT('NY:CA:IL:TX:AZ:PA:WA:CO:TN:FL', ':')[MOD(rn*3,10)] AS state
  FROM src
)
SELECT
    rn AS CUSTOMER_KEY,
    CONCAT('CUST', LPAD(CAST(rn AS STRING), 6, '0')) AS CUSTOMER_ID,
    fn, ln,
    LOWER(CONCAT(fn, '.', ln, rn, '@example.com')) AS EMAIL,
    CASE MOD(rn,3) WHEN 0 THEN 'M' WHEN 1 THEN 'F' ELSE 'O' END AS GENDER,
    IF(MOD(rn,20)=0, NULL,
       DATE_SUB(DATE'2023-01-01', CAST(FLOOR(RAND(rn)*18000 + 6570) AS INT))) AS BIRTH_DATE,
    city AS CITY, state AS STATE, 'US' AS COUNTRY,
    CASE MOD(rn,10)
        WHEN 0 THEN 'Platinum' WHEN 1 THEN 'Gold' WHEN 2 THEN 'Gold'
        WHEN 3 THEN 'Silver'   WHEN 4 THEN 'Silver' WHEN 5 THEN 'Silver'
        ELSE 'Bronze' END AS CUSTOMER_SEGMENT,
    DATE_SUB(DATE'2023-01-01', CAST(FLOOR(RAND(rn+500)*1065 + 30) AS INT)) AS REGISTRATION_DATE
FROM names;

-- =============================================================================
-- 3. DIM_PRODUCT
-- =============================================================================
CREATE OR REPLACE TABLE DIM_PRODUCT (
    PRODUCT_KEY   INT     NOT NULL,
    PRODUCT_ID    STRING  NOT NULL,
    PRODUCT_NAME  STRING  NOT NULL,
    CATEGORY      STRING  NOT NULL,
    SUBCATEGORY   STRING  NOT NULL,
    BRAND         STRING  NOT NULL,
    UNIT_COST     DECIMAL(10,2) NOT NULL,
    UNIT_PRICE    DECIMAL(10,2) NOT NULL,
    MARGIN_PCT    DECIMAL(5,2)  NOT NULL,
    IS_ACTIVE     BOOLEAN NOT NULL DEFAULT TRUE
)
COMMENT 'Product catalog — 200 SKUs across Electronics, Clothing, Home, Sports, Food';

INSERT INTO DIM_PRODUCT
WITH src AS (SELECT pos + 1 AS rn FROM (SELECT EXPLODE(SEQUENCE(0, 199)) AS pos)),
cats AS (
  SELECT rn,
    CASE WHEN rn<=50  THEN 'Electronics' WHEN rn<=100 THEN 'Clothing'
         WHEN rn<=140 THEN 'Home & Living' WHEN rn<=170 THEN 'Sports'
         ELSE 'Food & Beverage' END AS category,
    CASE WHEN rn<=10  THEN 'Smartphones'   WHEN rn<=20  THEN 'Laptops'
         WHEN rn<=30  THEN 'Tablets'       WHEN rn<=40  THEN 'Headphones'
         WHEN rn<=50  THEN 'Cameras'       WHEN rn<=65  THEN 'T-Shirts'
         WHEN rn<=80  THEN 'Jeans'         WHEN rn<=90  THEN 'Dresses'
         WHEN rn<=100 THEN 'Footwear'      WHEN rn<=115 THEN 'Furniture'
         WHEN rn<=125 THEN 'Kitchen'       WHEN rn<=135 THEN 'Bath & Bedding'
         WHEN rn<=140 THEN 'Decor'         WHEN rn<=155 THEN 'Fitness'
         WHEN rn<=170 THEN 'Outdoor Gear'  WHEN rn<=185 THEN 'Coffee & Tea'
         ELSE 'Snacks' END AS subcategory,
    SPLIT(CASE WHEN rn<=50  THEN 'TechPro:NovaTech:EliteSmart:ApexDigital:CoreTech'
               WHEN rn<=100 THEN 'UrbanStyle:FashionX:TrendWear:ModernFit:ClassicLine'
               WHEN rn<=140 THEN 'HomeComfort:LivingPlus:CozySpace:UrbanNest:NestWell'
               WHEN rn<=170 THEN 'ActivePeak:ProSport:OutdoorEdge:StrideOn:FitMax'
               ELSE              'NaturaBite:FreshPick:OrganicJoy:NutriBlend:TasteBest' END,
          ':')[MOD(rn-1, 5)] AS brand,
    ROUND(CASE WHEN rn<=10  THEN RAND(rn)*500+300  WHEN rn<=20  THEN RAND(rn)*700+500
               WHEN rn<=30  THEN RAND(rn)*400+200  WHEN rn<=40  THEN RAND(rn)*250+50
               WHEN rn<=50  THEN RAND(rn)*400+100  WHEN rn<=80  THEN RAND(rn)*52+8
               WHEN rn<=100 THEN RAND(rn)*105+15   WHEN rn<=140 THEN RAND(rn)*285+15
               WHEN rn<=170 THEN RAND(rn)*185+15   ELSE              RAND(rn)*28+2
          END, 2) AS unit_cost,
    ROUND(RAND(rn+200)*0.55 + 1.25, 4) AS markup
  FROM src
)
SELECT
    rn AS PRODUCT_KEY,
    CONCAT('PROD', LPAD(CAST(rn AS STRING),6,'0')) AS PRODUCT_ID,
    CONCAT(brand, ' ', subcategory, ' #', LPAD(CAST(MOD(rn-1,30)+1 AS STRING),3,'0')) AS PRODUCT_NAME,
    category, subcategory, brand,
    unit_cost AS UNIT_COST,
    ROUND(unit_cost * markup, 2) AS UNIT_PRICE,
    ROUND((1.0 - 1.0/markup)*100, 2) AS MARGIN_PCT,
    TRUE AS IS_ACTIVE
FROM cats;

-- =============================================================================
-- 4. DIM_STORE
-- =============================================================================
CREATE OR REPLACE TABLE DIM_STORE (
    STORE_KEY      INT     NOT NULL,
    STORE_ID       STRING  NOT NULL,
    STORE_NAME     STRING  NOT NULL,
    CITY           STRING  NOT NULL,
    STATE          STRING  NOT NULL,
    REGION         STRING  NOT NULL,
    STORE_TYPE     STRING  NOT NULL,
    OPEN_DATE      DATE    NOT NULL,
    SQUARE_FOOTAGE INT
)
COMMENT 'Store locations — 50 stores across 10 US cities';

INSERT INTO DIM_STORE
WITH src AS (SELECT pos + 1 AS rn FROM (SELECT EXPLODE(SEQUENCE(0, 49)) AS pos))
SELECT
    rn AS STORE_KEY,
    CONCAT('STR', LPAD(CAST(rn AS STRING),4,'0')) AS STORE_ID,
    CONCAT(SPLIT('Flagship:Standard:Outlet',':')[MOD(rn-1,3)], ' — ',
           SPLIT('New York:Los Angeles:Chicago:Houston:Phoenix:Philadelphia:Seattle:Denver:Nashville:Miami',':')[MOD(rn-1,10)]) AS STORE_NAME,
    SPLIT('New York:Los Angeles:Chicago:Houston:Phoenix:Philadelphia:Seattle:Denver:Nashville:Miami',':')[MOD(rn-1,10)] AS CITY,
    SPLIT('NY:CA:IL:TX:AZ:PA:WA:CO:TN:FL',':')[MOD(rn-1,10)] AS STATE,
    SPLIT('Northeast:West:Midwest:South:Southwest:Northeast:West:Mountain:South:Southeast',':')[MOD(rn-1,10)] AS REGION,
    SPLIT('Flagship:Standard:Outlet',':')[MOD(rn-1,3)] AS STORE_TYPE,
    DATE_SUB(DATE'2023-01-01', CAST(FLOOR(RAND(rn)*3285+365) AS INT)) AS OPEN_DATE,
    CASE MOD(rn-1,3)
        WHEN 0 THEN CAST(FLOOR(RAND(rn)*7000+8000) AS INT)
        WHEN 1 THEN CAST(FLOOR(RAND(rn)*4000+3000) AS INT)
        ELSE        CAST(FLOOR(RAND(rn)*2000+1500) AS INT)
    END AS SQUARE_FOOTAGE
FROM src;

-- =============================================================================
-- 5. DIM_EMPLOYEE
-- =============================================================================
CREATE OR REPLACE TABLE DIM_EMPLOYEE (
    EMPLOYEE_KEY INT     NOT NULL,
    EMPLOYEE_ID  STRING  NOT NULL,
    FIRST_NAME   STRING  NOT NULL,
    LAST_NAME    STRING  NOT NULL,
    JOB_TITLE    STRING  NOT NULL,
    DEPARTMENT   STRING  NOT NULL,
    STORE_KEY    INT     NOT NULL,
    HIRE_DATE    DATE    NOT NULL,
    IS_ACTIVE    BOOLEAN NOT NULL DEFAULT TRUE
)
COMMENT 'Employee dimension — 100 records. ~5% IS_ACTIVE=FALSE';

INSERT INTO DIM_EMPLOYEE
WITH src AS (SELECT pos + 1 AS rn FROM (SELECT EXPLODE(SEQUENCE(0, 99)) AS pos))
SELECT
    rn AS EMPLOYEE_KEY,
    CONCAT('EMP', LPAD(CAST(rn AS STRING),5,'0')) AS EMPLOYEE_ID,
    SPLIT('James:Mary:John:Patricia:Robert:Jennifer:Michael:Linda:William:Barbara:David:Elizabeth:Richard:Susan:Joseph:Jessica:Thomas:Sarah:Charles:Karen',':')[MOD(rn-1,20)] AS FIRST_NAME,
    SPLIT('Smith:Johnson:Williams:Brown:Jones:Garcia:Miller:Davis:Rodriguez:Martinez:Hernandez:Lopez:Gonzalez:Wilson:Anderson:Thomas:Taylor:Moore:Jackson:Martin',':')[MOD(rn*11+3,20)] AS LAST_NAME,
    SPLIT('Sales Associate:Senior Sales Associate:Department Manager:Assistant Manager:Store Manager:Cashier',':')[MOD(rn-1,6)] AS JOB_TITLE,
    SPLIT('Sales:Customer Service:Operations:Management',':')[MOD(rn-1,4)] AS DEPARTMENT,
    MOD(rn-1,50)+1 AS STORE_KEY,
    DATE_SUB(DATE'2023-01-01', CAST(FLOOR(RAND(rn)*2160+30) AS INT)) AS HIRE_DATE,
    IF(MOD(rn,20)=0, FALSE, TRUE) AS IS_ACTIVE
FROM src;

-- =============================================================================
-- 6. DIM_PROMOTION
-- =============================================================================
CREATE OR REPLACE TABLE DIM_PROMOTION (
    PROMOTION_KEY    INT     NOT NULL,
    PROMOTION_ID     STRING  NOT NULL,
    PROMOTION_NAME   STRING  NOT NULL,
    PROMOTION_TYPE   STRING  NOT NULL,
    DISCOUNT_PERCENT DECIMAL(5,2) NOT NULL,
    START_DATE       DATE    NOT NULL,
    END_DATE         DATE    NOT NULL,
    IS_ACTIVE        BOOLEAN NOT NULL DEFAULT TRUE
)
COMMENT 'Promotions — 30 campaigns. 20 active, 10 expired';

INSERT INTO DIM_PROMOTION
WITH src AS (SELECT pos + 1 AS rn FROM (SELECT EXPLODE(SEQUENCE(0, 29)) AS pos)),
types AS (
  SELECT rn,
    SPLIT('Seasonal:Loyalty:Flash:Clearance',':')[MOD(rn-1,4)] AS promo_type,
    ROUND(CASE MOD(rn-1,4)
        WHEN 0 THEN RAND(rn)*15+10  WHEN 1 THEN RAND(rn)*10+5
        WHEN 2 THEN RAND(rn)*20+20  ELSE        RAND(rn)*20+30
    END, 2) AS disc_pct,
    DATE_ADD(DATE'2023-01-01', (rn-1)*24) AS start_dt
  FROM src
)
SELECT
    rn AS PROMOTION_KEY,
    CONCAT('PROMO', LPAD(CAST(rn AS STRING),5,'0')) AS PROMOTION_ID,
    CONCAT(promo_type, ' Event #', rn) AS PROMOTION_NAME,
    promo_type AS PROMOTION_TYPE,
    disc_pct AS DISCOUNT_PERCENT,
    start_dt AS START_DATE,
    DATE_ADD(start_dt, CAST(FLOOR(RAND(rn)*23+7) AS INT)) AS END_DATE,
    IF(rn<=20, TRUE, FALSE) AS IS_ACTIVE
FROM types;

-- =============================================================================
-- 7. FACT_SALES
-- =============================================================================
CREATE OR REPLACE TABLE FACT_SALES (
    SALE_KEY        INT     NOT NULL,
    DATE_KEY        INT     NOT NULL,
    CUSTOMER_KEY    INT     NOT NULL,
    PRODUCT_KEY     INT     NOT NULL,
    STORE_KEY       INT     NOT NULL,
    EMPLOYEE_KEY    INT     NOT NULL,
    PROMOTION_KEY   INT,
    TRANSACTION_ID  STRING  NOT NULL,
    PAYMENT_METHOD  STRING  NOT NULL,
    QUANTITY        INT     NOT NULL,
    UNIT_PRICE      DECIMAL(10,2) NOT NULL,
    UNIT_COST       DECIMAL(10,2) NOT NULL,
    DISCOUNT_AMOUNT DECIMAL(10,2) NOT NULL DEFAULT 0,
    GROSS_AMOUNT    DECIMAL(10,2) NOT NULL,
    NET_AMOUNT      DECIMAL(10,2) NOT NULL,
    TAX_AMOUNT      DECIMAL(10,2) NOT NULL,
    MARGIN_AMOUNT   DECIMAL(10,2) NOT NULL
)
COMMENT 'Fact table — 10,000 sales transactions Jan 2023 – Dec 2024';

INSERT INTO FACT_SALES
WITH raw AS (
    SELECT
        pos + 1 AS rn,
        DATE_ADD(DATE'2023-01-01', CAST(FLOOR(RAND(pos)*730) AS INT)) AS sale_dt,
        CAST(FLOOR(RAND(pos+1)*500)+1 AS INT)  AS cust_k,
        CAST(FLOOR(RAND(pos+2)*200)+1 AS INT)  AS prod_k,
        CAST(FLOOR(RAND(pos+3)*50)+1  AS INT)  AS store_k,
        CAST(FLOOR(RAND(pos+4)*100)+1 AS INT)  AS emp_k,
        IF(RAND(pos+5)<0.30, CAST(FLOOR(RAND(pos+6)*30)+1 AS INT), NULL) AS promo_k,
        GREATEST(1, LEAST(10, CAST(ROUND(RANDN(pos)*1.5+2.5) AS INT))) AS qty
    FROM (SELECT EXPLODE(SEQUENCE(0, 9999)) AS pos)
)
SELECT
    raw.rn AS SALE_KEY,
    CAST(DATE_FORMAT(raw.sale_dt, 'yyyyMMdd') AS INT) AS DATE_KEY,
    raw.cust_k  AS CUSTOMER_KEY,
    raw.prod_k  AS PRODUCT_KEY,
    raw.store_k AS STORE_KEY,
    raw.emp_k   AS EMPLOYEE_KEY,
    raw.promo_k AS PROMOTION_KEY,
    CONCAT('TXN', LPAD(CAST(raw.rn AS STRING), 8, '0')) AS TRANSACTION_ID,
    SPLIT('CREDIT_CARD:DEBIT_CARD:CASH:PAYPAL:APPLE_PAY', ':')[MOD(raw.rn-1,5)] AS PAYMENT_METHOD,
    raw.qty AS QUANTITY,
    p.UNIT_PRICE, p.UNIT_COST,
    ROUND(p.UNIT_PRICE * raw.qty * COALESCE(pr.DISCOUNT_PERCENT, 0) / 100.0, 2) AS DISCOUNT_AMOUNT,
    ROUND(p.UNIT_PRICE * raw.qty, 2)                                             AS GROSS_AMOUNT,
    ROUND(p.UNIT_PRICE * raw.qty * (1.0 - COALESCE(pr.DISCOUNT_PERCENT,0)/100.0), 2) AS NET_AMOUNT,
    ROUND(p.UNIT_PRICE * raw.qty * (1.0 - COALESCE(pr.DISCOUNT_PERCENT,0)/100.0) * 0.08, 2) AS TAX_AMOUNT,
    ROUND((p.UNIT_PRICE - p.UNIT_COST) * raw.qty * (1.0 - COALESCE(pr.DISCOUNT_PERCENT,0)/100.0), 2) AS MARGIN_AMOUNT
FROM raw
JOIN      DIM_PRODUCT   p  ON p.PRODUCT_KEY   = raw.prod_k
LEFT JOIN DIM_PROMOTION pr ON pr.PROMOTION_KEY = raw.promo_k;

-- =============================================================================
-- 8. Verification
-- =============================================================================
SELECT 'DIM_DATE'       AS tbl, COUNT(*) AS rows FROM DIM_DATE
UNION ALL SELECT 'DIM_CUSTOMER',  COUNT(*) FROM DIM_CUSTOMER
UNION ALL SELECT 'DIM_PRODUCT',   COUNT(*) FROM DIM_PRODUCT
UNION ALL SELECT 'DIM_STORE',     COUNT(*) FROM DIM_STORE
UNION ALL SELECT 'DIM_EMPLOYEE',  COUNT(*) FROM DIM_EMPLOYEE
UNION ALL SELECT 'DIM_PROMOTION', COUNT(*) FROM DIM_PROMOTION
UNION ALL SELECT 'FACT_SALES',    COUNT(*) FROM FACT_SALES
ORDER BY 1;
