-- =============================================================================
-- SAMPLE_DW.RETAIL — Rich Metadata Comments
-- Run AFTER setup_snowflake.sql
-- Adds COMMENT ON TABLE and COMMENT ON COLUMN for all 7 tables (207 objects).
-- =============================================================================

USE WAREHOUSE PROFILER_APP;   -- ← change if needed
USE DATABASE SAMPLE_DW;
USE SCHEMA   RETAIL;

-- =============================================================================
-- DIM_DATE
-- =============================================================================
COMMENT ON TABLE DIM_DATE IS
'Date dimension covering 2023-01-01 to 2024-12-31 (730 rows). Provides full calendar attributes for time-based slicing and dicing. Includes weekend flags and US federal holiday markers. Join to FACT_SALES on DATE_KEY.';

COMMENT ON COLUMN DIM_DATE.DATE_KEY IS
'Surrogate key in YYYYMMDD integer format (e.g. 20230101). Used as FK in FACT_SALES.DATE_KEY. Integer format enables fast range predicates without date casting.';

COMMENT ON COLUMN DIM_DATE.FULL_DATE IS
'Calendar date value (DATE type). Use this column for date arithmetic and display; prefer DATE_KEY for joins.';

COMMENT ON COLUMN DIM_DATE.DAY_OF_WEEK IS
'Day-of-week number following Snowflake convention: 0=Sunday, 1=Monday, 2=Tuesday, 3=Wednesday, 4=Thursday, 5=Friday, 6=Saturday.';

COMMENT ON COLUMN DIM_DATE.DAY_NAME IS
'Full English name of the day (e.g. Monday, Tuesday). Suitable for display labels and GROUP BY in reports.';

COMMENT ON COLUMN DIM_DATE.DAY_OF_MONTH IS
'Day number within the month, 1–31. Useful for end-of-month analysis and payroll/billing cycle reports.';

COMMENT ON COLUMN DIM_DATE.DAY_OF_YEAR IS
'Day number within the calendar year, 1–366. Useful for year-over-year comparisons aligned to the same calendar position.';

COMMENT ON COLUMN DIM_DATE.WEEK_OF_YEAR IS
'ISO week number within the year, 1–53. Weeks start on Monday per ISO 8601. Use for weekly trend aggregations.';

COMMENT ON COLUMN DIM_DATE.MONTH_NUM IS
'Month number, 1=January through 12=December. Used for monthly aggregations and period-over-period comparisons.';

COMMENT ON COLUMN DIM_DATE.MONTH_NAME IS
'Full English month name (e.g. January, February). Suitable for report axis labels; order by MONTH_NUM not alphabetically.';

COMMENT ON COLUMN DIM_DATE.QUARTER IS
'Calendar quarter, 1–4. Q1=Jan–Mar, Q2=Apr–Jun, Q3=Jul–Sep, Q4=Oct–Dec. Key dimension for quarterly business reviews.';

COMMENT ON COLUMN DIM_DATE.YEAR IS
'4-digit calendar year (2023 or 2024 in this dataset). Used as the outermost grouping in year-over-year trend analysis.';

COMMENT ON COLUMN DIM_DATE.IS_WEEKEND IS
'TRUE if the day is Saturday (DAY_OF_WEEK=6) or Sunday (DAY_OF_WEEK=0). Useful for filtering to business days or analyzing weekend vs weekday sales patterns.';

COMMENT ON COLUMN DIM_DATE.IS_HOLIDAY IS
'TRUE if the date is a US federal holiday in this dataset: New Year''s Day, Independence Day, Thanksgiving, Christmas (2023 and 2024). Holiday flag affects staffing models and promotional planning.';

-- =============================================================================
-- DIM_CUSTOMER
-- =============================================================================
COMMENT ON TABLE DIM_CUSTOMER IS
'Customer master dimension with 500 synthetic records. Covers demographics, geography, and loyalty segmentation. Approximately 5% of records have NULL BIRTH_DATE, intentionally inserted as a data-quality test case. Segment distribution: Bronze 40%, Silver 30%, Gold 20%, Platinum 10%. Join to FACT_SALES on CUSTOMER_KEY.';

COMMENT ON COLUMN DIM_CUSTOMER.CUSTOMER_KEY IS
'Surrogate key (1–500). References FACT_SALES.CUSTOMER_KEY. Sequential integer assigned at load time.';

COMMENT ON COLUMN DIM_CUSTOMER.CUSTOMER_ID IS
'Business/natural key in format CUST000001. Stable across system migrations; use this for cross-system joins rather than the surrogate key.';

COMMENT ON COLUMN DIM_CUSTOMER.FIRST_NAME IS
'Customer first name. Synthetic data drawn from a pool of 20 common US first names.';

COMMENT ON COLUMN DIM_CUSTOMER.LAST_NAME IS
'Customer last name. Synthetic data drawn from a pool of 26 common US last names.';

COMMENT ON COLUMN DIM_CUSTOMER.EMAIL IS
'Synthetic email address in the format firstname.lastnameid@example.com. All addresses use the example.com domain — safe for testing without real PII exposure.';

COMMENT ON COLUMN DIM_CUSTOMER.GENDER IS
'Customer self-reported gender: M=Male, F=Female, O=Other/Non-binary. Null-free in this dataset; real data may require handling of unknown/declined values.';

COMMENT ON COLUMN DIM_CUSTOMER.BIRTH_DATE IS
'Customer date of birth. Approximately 5% of rows are intentionally NULL to serve as a data-quality alert test. Age range in this dataset: 18–70 years as of 2023-01-01.';

COMMENT ON COLUMN DIM_CUSTOMER.CITY IS
'US city of primary residence. Ten cities are represented: New York, Los Angeles, Chicago, Houston, Phoenix, Philadelphia, Seattle, Denver, Nashville, Miami.';

COMMENT ON COLUMN DIM_CUSTOMER.STATE IS
'2-letter US state code corresponding to CITY (e.g. NY for New York, CA for Los Angeles). Always consistent with CITY in this dataset.';

COMMENT ON COLUMN DIM_CUSTOMER.COUNTRY IS
'ISO country code. All records are ''US'' in this dataset. Included for future international expansion support.';

COMMENT ON COLUMN DIM_CUSTOMER.CUSTOMER_SEGMENT IS
'Loyalty tier driving promotional eligibility and service levels. Platinum (~10%): top spenders; Gold (~20%): high-value regulars; Silver (~30%): mid-tier; Bronze (~40%): entry-level or infrequent buyers.';

COMMENT ON COLUMN DIM_CUSTOMER.REGISTRATION_DATE IS
'Date the customer first registered in the system. All dates fall between 2020 and 2023 in this synthetic dataset. Use for customer tenure calculations.';

-- =============================================================================
-- DIM_PRODUCT
-- =============================================================================
COMMENT ON TABLE DIM_PRODUCT IS
'Product catalog dimension with 200 synthetic SKUs across 5 top-level categories: Electronics (50), Clothing (50), Home & Living (40), Sports (30), Food & Beverage (30). All products are active (IS_ACTIVE=TRUE). Unit prices are cost × markup factor (1.25–1.80×). Join to FACT_SALES on PRODUCT_KEY.';

COMMENT ON COLUMN DIM_PRODUCT.PRODUCT_KEY IS
'Surrogate key (1–200). References FACT_SALES.PRODUCT_KEY. Products 1–50 are Electronics, 51–100 Clothing, 101–140 Home & Living, 141–170 Sports, 171–200 Food & Beverage.';

COMMENT ON COLUMN DIM_PRODUCT.PRODUCT_ID IS
'Business/natural key in format PROD000001. Stable identifier for cross-system reconciliation.';

COMMENT ON COLUMN DIM_PRODUCT.PRODUCT_NAME IS
'Descriptive product name composed of brand + subcategory + sequence number (e.g. "TechPro Smartphones #001"). Not unique across brands.';

COMMENT ON COLUMN DIM_PRODUCT.CATEGORY IS
'Top-level product category: Electronics, Clothing, Home & Living, Sports, Food & Beverage. Primary grouping dimension for category management and P&L reporting.';

COMMENT ON COLUMN DIM_PRODUCT.SUBCATEGORY IS
'Second-level grouping within CATEGORY. Electronics → Smartphones/Laptops/Tablets/Headphones/Cameras. Clothing → T-Shirts/Jeans/Dresses/Footwear. Home → Furniture/Kitchen/Bath & Bedding/Decor. Sports → Fitness/Outdoor Gear. Food → Coffee & Tea/Snacks.';

COMMENT ON COLUMN DIM_PRODUCT.BRAND IS
'Brand associated with the product. Five synthetic brands per category (e.g. TechPro, NovaTech for Electronics). Use for brand-level market share analysis.';

COMMENT ON COLUMN DIM_PRODUCT.UNIT_COST IS
'Wholesale cost per unit in USD at time of catalog creation. Used with UNIT_PRICE to compute gross margin. Electronics: $50–$1,200; Clothing: $8–$120; Home: $15–$300; Sports: $15–$200; Food: $2–$30.';

COMMENT ON COLUMN DIM_PRODUCT.UNIT_PRICE IS
'Retail selling price per unit in USD. Always greater than UNIT_COST. Computed as UNIT_COST × random markup factor (1.25–1.80×). This is the list price; actual transaction price is in FACT_SALES.UNIT_PRICE.';

COMMENT ON COLUMN DIM_PRODUCT.MARGIN_PCT IS
'Gross margin percentage: (UNIT_PRICE - UNIT_COST) / UNIT_PRICE × 100. Ranges from ~20% to ~44% across the catalog depending on the markup factor. Used in profitability analysis.';

COMMENT ON COLUMN DIM_PRODUCT.IS_ACTIVE IS
'Product availability flag. TRUE = available for sale. FALSE = discontinued or out of stock. All 200 products are TRUE in this dataset; real catalogs typically have 10–30% inactive SKUs.';

-- =============================================================================
-- DIM_STORE
-- =============================================================================
COMMENT ON TABLE DIM_STORE IS
'Store location dimension with 50 physical retail locations across 10 US cities (5 stores per city). Three store formats: Flagship (large format, premium experience), Standard (mid-size), Outlet (discount/clearance). Join to FACT_SALES on STORE_KEY.';

COMMENT ON COLUMN DIM_STORE.STORE_KEY IS
'Surrogate key (1–50). References FACT_SALES.STORE_KEY. Also referenced by DIM_EMPLOYEE.STORE_KEY to indicate where each employee is based.';

COMMENT ON COLUMN DIM_STORE.STORE_ID IS
'Business/natural key in format STR0001. Stable identifier for POS system and inventory integrations.';

COMMENT ON COLUMN DIM_STORE.STORE_NAME IS
'Human-readable store name combining format type and city (e.g. "Flagship — New York"). Not guaranteed to be unique across all records.';

COMMENT ON COLUMN DIM_STORE.CITY IS
'City where the store is located. Ten cities: New York, Los Angeles, Chicago, Houston, Phoenix, Philadelphia, Seattle, Denver, Nashville, Miami.';

COMMENT ON COLUMN DIM_STORE.STATE IS
'2-letter US state code. Consistent with CITY: NY, CA, IL, TX, AZ, PA, WA, CO, TN, FL.';

COMMENT ON COLUMN DIM_STORE.REGION IS
'Geographic sales region: Northeast, West, Midwest, South, Southwest, Mountain, Southeast. Used for regional performance reporting and territory management.';

COMMENT ON COLUMN DIM_STORE.STORE_TYPE IS
'Store format tier. Flagship: premium locations, 8,000–15,000 sqft, full assortment. Standard: core format, 3,000–7,000 sqft. Outlet: discount/clearance, 1,500–3,500 sqft, limited assortment.';

COMMENT ON COLUMN DIM_STORE.OPEN_DATE IS
'Date the store first opened for business. Ranges from ~2013 to ~2022 in this dataset. Used to calculate store age and cohort performance analysis.';

COMMENT ON COLUMN DIM_STORE.SQUARE_FOOTAGE IS
'Total retail floor area in square feet. Correlates with store type: Flagship 8,000–15,000, Standard 3,000–7,000, Outlet 1,500–3,500. Used in sales-per-sqft productivity metrics.';

-- =============================================================================
-- DIM_EMPLOYEE
-- =============================================================================
COMMENT ON TABLE DIM_EMPLOYEE IS
'Employee dimension with 100 synthetic retail staff records. Each employee is assigned to exactly one store. Approximately 5% of records are inactive (IS_ACTIVE=FALSE), representing departed staff. Six job titles across four departments. Join to FACT_SALES on EMPLOYEE_KEY.';

COMMENT ON COLUMN DIM_EMPLOYEE.EMPLOYEE_KEY IS
'Surrogate key (1–100). References FACT_SALES.EMPLOYEE_KEY. Employees are distributed across all 50 stores (2 employees per store on average, using MOD assignment).';

COMMENT ON COLUMN DIM_EMPLOYEE.EMPLOYEE_ID IS
'Business/natural key in format EMP00001. Used in HR systems, payroll, and store scheduling integrations.';

COMMENT ON COLUMN DIM_EMPLOYEE.FIRST_NAME IS
'Employee first name. Synthetic data.';

COMMENT ON COLUMN DIM_EMPLOYEE.LAST_NAME IS
'Employee last name. Synthetic data.';

COMMENT ON COLUMN DIM_EMPLOYEE.JOB_TITLE IS
'Current job title. Values: Sales Associate, Senior Sales Associate, Department Manager, Assistant Manager, Store Manager, Cashier. Determines access level and commission structure.';

COMMENT ON COLUMN DIM_EMPLOYEE.DEPARTMENT IS
'Functional department: Sales (revenue-generating floor staff), Customer Service (returns and support), Operations (logistics and stock), Management (store leadership).';

COMMENT ON COLUMN DIM_EMPLOYEE.STORE_KEY IS
'FK to DIM_STORE.STORE_KEY. Identifies the primary store where this employee is based. Enables store-level headcount and productivity analysis.';

COMMENT ON COLUMN DIM_EMPLOYEE.HIRE_DATE IS
'Date the employee was hired. Ranges from ~2017 to ~2022 in this dataset. Used for tenure calculations and retention analysis.';

COMMENT ON COLUMN DIM_EMPLOYEE.IS_ACTIVE IS
'Employment status flag. TRUE = currently employed. FALSE = departed (resigned, terminated, or retired). ~5% of records are FALSE. Always filter on IS_ACTIVE=TRUE for headcount reporting.';

-- =============================================================================
-- DIM_PROMOTION
-- =============================================================================
COMMENT ON TABLE DIM_PROMOTION IS
'Promotion/campaign dimension with 30 records across 4 promotion types. Discounts range from 5% (Loyalty) to 50% (Clearance). Promotions 1–20 are active; 21–30 are expired. Approximately 30% of FACT_SALES rows reference a promotion (PROMOTION_KEY IS NOT NULL). Join to FACT_SALES on PROMOTION_KEY.';

COMMENT ON COLUMN DIM_PROMOTION.PROMOTION_KEY IS
'Surrogate key (1–30). Nullable FK in FACT_SALES.PROMOTION_KEY. NULL in FACT_SALES means no promotion was applied to that transaction.';

COMMENT ON COLUMN DIM_PROMOTION.PROMOTION_ID IS
'Business/natural key in format PROMO00001. Used for campaign tracking in marketing systems.';

COMMENT ON COLUMN DIM_PROMOTION.PROMOTION_NAME IS
'Descriptive promotion name combining type and sequence number (e.g. "Seasonal Event #1"). For real implementations, replace with campaign names from the marketing calendar.';

COMMENT ON COLUMN DIM_PROMOTION.PROMOTION_TYPE IS
'Promotion category driving the discount range: Seasonal (10–25%, tied to calendar events), Loyalty (5–15%, member-only), Flash (20–40%, short-duration urgency), Clearance (30–50%, end-of-life inventory reduction).';

COMMENT ON COLUMN DIM_PROMOTION.DISCOUNT_PERCENT IS
'Percentage discount applied to the order gross amount. Multiplied against UNIT_PRICE × QUANTITY to compute FACT_SALES.DISCOUNT_AMOUNT. Range: 5.00–50.00.';

COMMENT ON COLUMN DIM_PROMOTION.START_DATE IS
'Date the promotion becomes active. Promotions are staggered 24 days apart starting 2023-01-01, spanning the full 2023–2024 dataset window.';

COMMENT ON COLUMN DIM_PROMOTION.END_DATE IS
'Date the promotion expires (START_DATE + 7 to 30 days). The end date is informational; FACT_SALES does not enforce that the sale date falls within the promotion window.';

COMMENT ON COLUMN DIM_PROMOTION.IS_ACTIVE IS
'Current promotion status. TRUE for promotions 1–20 (still valid or recently active), FALSE for promotions 21–30 (expired). Filter IS_ACTIVE=TRUE for current campaign reporting.';

-- =============================================================================
-- FACT_SALES
-- =============================================================================
COMMENT ON TABLE FACT_SALES IS
'Central fact table storing 10,000 individual retail sales transactions across 2023-01-01 to 2024-12-31. Each row represents one line-item purchase by one customer at one store. References all six dimension tables. Approximately 30% of transactions have a promotion applied (PROMOTION_KEY IS NOT NULL). Financial metrics are pre-computed at load time: GROSS_AMOUNT, NET_AMOUNT, TAX_AMOUNT (8% flat rate), and MARGIN_AMOUNT.';

COMMENT ON COLUMN FACT_SALES.SALE_KEY IS
'Surrogate key. Sequential transaction identifier (1–10,000). Used as PK; not meaningful for business analysis.';

COMMENT ON COLUMN FACT_SALES.DATE_KEY IS
'FK to DIM_DATE.DATE_KEY (YYYYMMDD integer). Identifies the calendar date of the transaction. Never NULL.';

COMMENT ON COLUMN FACT_SALES.CUSTOMER_KEY IS
'FK to DIM_CUSTOMER.CUSTOMER_KEY. Identifies the purchasing customer. Never NULL. Use to join demographic and segment attributes.';

COMMENT ON COLUMN FACT_SALES.PRODUCT_KEY IS
'FK to DIM_PRODUCT.PRODUCT_KEY. Identifies the product purchased. Never NULL. Use to join category, brand, and price attributes.';

COMMENT ON COLUMN FACT_SALES.STORE_KEY IS
'FK to DIM_STORE.STORE_KEY. Identifies the store where the purchase occurred. Never NULL. Use to join location and format attributes.';

COMMENT ON COLUMN FACT_SALES.EMPLOYEE_KEY IS
'FK to DIM_EMPLOYEE.EMPLOYEE_KEY. Identifies the employee who processed the sale. Never NULL. Use to analyse sales performance by staff member or department.';

COMMENT ON COLUMN FACT_SALES.PROMOTION_KEY IS
'Nullable FK to DIM_PROMOTION.PROMOTION_KEY. NULL (~70% of rows) means no promotion was applied. When NOT NULL, the DISCOUNT_PERCENT from DIM_PROMOTION was used to compute DISCOUNT_AMOUNT.';

COMMENT ON COLUMN FACT_SALES.TRANSACTION_ID IS
'Business/natural key in format TXN00000001. Unique per sale. Used for receipt lookup and customer service queries.';

COMMENT ON COLUMN FACT_SALES.PAYMENT_METHOD IS
'Payment instrument used for the transaction: CREDIT_CARD, DEBIT_CARD, CASH, PAYPAL, APPLE_PAY. Cycled deterministically based on SALE_KEY; real data would show payment-method trends by segment and channel.';

COMMENT ON COLUMN FACT_SALES.QUANTITY IS
'Number of units purchased in this transaction (1–10). Normally distributed with mean 2.5 and std dev 1.5, clipped to the 1–10 range. Multiply by UNIT_PRICE to get GROSS_AMOUNT.';

COMMENT ON COLUMN FACT_SALES.UNIT_PRICE IS
'Selling price per unit at the time of sale, copied from DIM_PRODUCT.UNIT_PRICE. Stored here (Type 0 SCD) so historical sales are not affected by future price changes.';

COMMENT ON COLUMN FACT_SALES.UNIT_COST IS
'Wholesale cost per unit at the time of sale, copied from DIM_PRODUCT.UNIT_COST. Used with UNIT_PRICE to derive MARGIN_AMOUNT without joining to the product dimension.';

COMMENT ON COLUMN FACT_SALES.DISCOUNT_AMOUNT IS
'Total discount applied: UNIT_PRICE × QUANTITY × DISCOUNT_PERCENT / 100. Equals 0.00 when PROMOTION_KEY IS NULL. Always ≥ 0.';

COMMENT ON COLUMN FACT_SALES.GROSS_AMOUNT IS
'Revenue before any discount: UNIT_PRICE × QUANTITY. Represents the full list-price transaction value. Sum of GROSS_AMOUNT is the pre-discount topline revenue metric.';

COMMENT ON COLUMN FACT_SALES.NET_AMOUNT IS
'Actual revenue received: GROSS_AMOUNT − DISCOUNT_AMOUNT. This is the primary revenue KPI. Net revenue is the basis for TAX_AMOUNT and MARGIN_AMOUNT calculations.';

COMMENT ON COLUMN FACT_SALES.TAX_AMOUNT IS
'Sales tax charged: NET_AMOUNT × 0.08 (8% flat rate, simplified for this dataset). Not included in revenue metrics but required for statutory reporting.';

COMMENT ON COLUMN FACT_SALES.MARGIN_AMOUNT IS
'Gross profit in dollars: (UNIT_PRICE − UNIT_COST) × QUANTITY × (1 − DISCOUNT_PERCENT/100). Represents the contribution to overhead and operating profit. Divide by NET_AMOUNT for gross margin percentage.';

-- =============================================================================
-- Verification: confirm all comments were applied
-- =============================================================================
SELECT
    t.TABLE_NAME,
    t.COMMENT                               AS TABLE_COMMENT,
    COUNT(c.COLUMN_NAME)                    AS col_count,
    COUNT(c.COMMENT)                        AS cols_with_comment,
    COUNT(c.COLUMN_NAME) - COUNT(c.COMMENT) AS cols_missing_comment
FROM INFORMATION_SCHEMA.TABLES  t
JOIN INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
 AND c.TABLE_NAME   = t.TABLE_NAME
WHERE t.TABLE_SCHEMA = 'RETAIL'
  AND t.TABLE_TYPE   = 'BASE TABLE'
GROUP BY t.TABLE_NAME, t.COMMENT
ORDER BY t.TABLE_NAME;
