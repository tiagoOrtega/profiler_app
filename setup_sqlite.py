"""
Create a sample SQLite database with the same Retail Star Schema
used by setup_snowflake.sql.

Usage:
    python setup_sqlite.py                  # creates ./sample.db
    python setup_sqlite.py /path/to/db.db   # custom path

Tables   Rows
-------  ----
DIM_DATE          730
DIM_CUSTOMER      500
DIM_PRODUCT       200
DIM_STORE          50
DIM_EMPLOYEE      100
DIM_PROMOTION      30
FACT_SALES      10 000
"""

import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

_SEED = 42


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pick(lst, n):
    return lst[n % len(lst)]


_FIRST = ["James","Mary","John","Patricia","Robert","Jennifer","Michael","Linda",
          "William","Barbara","David","Elizabeth","Richard","Susan","Joseph",
          "Jessica","Thomas","Sarah","Charles","Karen"]

_LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
          "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson",
          "Thomas","Taylor","Moore","Jackson","Martin","Lee","Thompson","White",
          "Harris","Sanchez","Clark"]

_CITIES = ["New York","Los Angeles","Chicago","Houston","Phoenix",
           "Philadelphia","Seattle","Denver","Nashville","Miami"]
_STATES  = ["NY","CA","IL","TX","AZ","PA","WA","CO","TN","FL"]

_START = date(2023, 1, 1)
_END   = date(2024, 12, 31)


def _rand_date(rng, start=_START, end=_END) -> str:
    delta = (end - start).days
    return str(start + timedelta(days=rng.randint(0, delta)))


# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS DIM_DATE (
    DATE_KEY      INTEGER PRIMARY KEY,
    FULL_DATE     TEXT    NOT NULL,
    DAY_OF_WEEK   INTEGER NOT NULL,
    DAY_NAME      TEXT    NOT NULL,
    DAY_OF_MONTH  INTEGER NOT NULL,
    DAY_OF_YEAR   INTEGER NOT NULL,
    WEEK_OF_YEAR  INTEGER NOT NULL,
    MONTH_NUM     INTEGER NOT NULL,
    MONTH_NAME    TEXT    NOT NULL,
    QUARTER       INTEGER NOT NULL,
    YEAR          INTEGER NOT NULL,
    IS_WEEKEND    INTEGER NOT NULL,
    IS_HOLIDAY    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS DIM_CUSTOMER (
    CUSTOMER_KEY      INTEGER PRIMARY KEY,
    CUSTOMER_ID       TEXT    NOT NULL UNIQUE,
    FIRST_NAME        TEXT    NOT NULL,
    LAST_NAME         TEXT    NOT NULL,
    EMAIL             TEXT    NOT NULL,
    GENDER            TEXT    NOT NULL,
    BIRTH_DATE        TEXT,
    CITY              TEXT,
    STATE             TEXT,
    COUNTRY           TEXT    DEFAULT 'US',
    CUSTOMER_SEGMENT  TEXT    NOT NULL,
    REGISTRATION_DATE TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS DIM_PRODUCT (
    PRODUCT_KEY   INTEGER PRIMARY KEY,
    PRODUCT_ID    TEXT    NOT NULL UNIQUE,
    PRODUCT_NAME  TEXT    NOT NULL,
    CATEGORY      TEXT    NOT NULL,
    SUBCATEGORY   TEXT    NOT NULL,
    BRAND         TEXT    NOT NULL,
    UNIT_COST     REAL    NOT NULL,
    UNIT_PRICE    REAL    NOT NULL,
    MARGIN_PCT    REAL    NOT NULL,
    IS_ACTIVE     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS DIM_STORE (
    STORE_KEY      INTEGER PRIMARY KEY,
    STORE_ID       TEXT    NOT NULL UNIQUE,
    STORE_NAME     TEXT    NOT NULL,
    CITY           TEXT    NOT NULL,
    STATE          TEXT    NOT NULL,
    REGION         TEXT    NOT NULL,
    STORE_TYPE     TEXT    NOT NULL,
    OPEN_DATE      TEXT    NOT NULL,
    SQUARE_FOOTAGE INTEGER
);

CREATE TABLE IF NOT EXISTS DIM_EMPLOYEE (
    EMPLOYEE_KEY  INTEGER PRIMARY KEY,
    EMPLOYEE_ID   TEXT    NOT NULL UNIQUE,
    FIRST_NAME    TEXT    NOT NULL,
    LAST_NAME     TEXT    NOT NULL,
    JOB_TITLE     TEXT    NOT NULL,
    DEPARTMENT    TEXT    NOT NULL,
    STORE_KEY     INTEGER NOT NULL,
    HIRE_DATE     TEXT    NOT NULL,
    IS_ACTIVE     INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (STORE_KEY) REFERENCES DIM_STORE(STORE_KEY)
);

CREATE TABLE IF NOT EXISTS DIM_PROMOTION (
    PROMOTION_KEY    INTEGER PRIMARY KEY,
    PROMOTION_ID     TEXT    NOT NULL UNIQUE,
    PROMOTION_NAME   TEXT    NOT NULL,
    PROMOTION_TYPE   TEXT    NOT NULL,
    DISCOUNT_PERCENT REAL    NOT NULL,
    START_DATE       TEXT    NOT NULL,
    END_DATE         TEXT    NOT NULL,
    IS_ACTIVE        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS FACT_SALES (
    SALE_KEY        INTEGER PRIMARY KEY,
    DATE_KEY        INTEGER NOT NULL,
    CUSTOMER_KEY    INTEGER NOT NULL,
    PRODUCT_KEY     INTEGER NOT NULL,
    STORE_KEY       INTEGER NOT NULL,
    EMPLOYEE_KEY    INTEGER NOT NULL,
    PROMOTION_KEY   INTEGER,
    TRANSACTION_ID  TEXT    NOT NULL UNIQUE,
    PAYMENT_METHOD  TEXT    NOT NULL,
    QUANTITY        INTEGER NOT NULL,
    UNIT_PRICE      REAL    NOT NULL,
    UNIT_COST       REAL    NOT NULL,
    DISCOUNT_AMOUNT REAL    NOT NULL DEFAULT 0,
    GROSS_AMOUNT    REAL    NOT NULL,
    NET_AMOUNT      REAL    NOT NULL,
    TAX_AMOUNT      REAL    NOT NULL,
    MARGIN_AMOUNT   REAL    NOT NULL,
    FOREIGN KEY (DATE_KEY)      REFERENCES DIM_DATE(DATE_KEY),
    FOREIGN KEY (CUSTOMER_KEY)  REFERENCES DIM_CUSTOMER(CUSTOMER_KEY),
    FOREIGN KEY (PRODUCT_KEY)   REFERENCES DIM_PRODUCT(PRODUCT_KEY),
    FOREIGN KEY (STORE_KEY)     REFERENCES DIM_STORE(STORE_KEY),
    FOREIGN KEY (EMPLOYEE_KEY)  REFERENCES DIM_EMPLOYEE(EMPLOYEE_KEY),
    FOREIGN KEY (PROMOTION_KEY) REFERENCES DIM_PROMOTION(PROMOTION_KEY)
);
"""

_HOLIDAYS = {
    "2023-01-01","2023-07-04","2023-11-23","2023-12-25",
    "2024-01-01","2024-07-04","2024-11-28","2024-12-25",
}

_MONTHS = ["January","February","March","April","May","June",
           "July","August","September","October","November","December"]
_DAYS   = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]

_CAT_MAP = [
    (10,  "Electronics", "Smartphones",    "TechPro:NovaTech:EliteSmart:ApexDigital:CoreTech",   300,  800),
    (20,  "Electronics", "Laptops",        "TechPro:NovaTech:EliteSmart:ApexDigital:CoreTech",   500, 1200),
    (30,  "Electronics", "Tablets",        "TechPro:NovaTech:EliteSmart:ApexDigital:CoreTech",   200,  600),
    (40,  "Electronics", "Headphones",     "TechPro:NovaTech:EliteSmart:ApexDigital:CoreTech",    50,  300),
    (50,  "Electronics", "Cameras",        "TechPro:NovaTech:EliteSmart:ApexDigital:CoreTech",   100,  500),
    (65,  "Clothing",    "T-Shirts",       "UrbanStyle:FashionX:TrendWear:ModernFit:ClassicLine",  8,   60),
    (80,  "Clothing",    "Jeans",          "UrbanStyle:FashionX:TrendWear:ModernFit:ClassicLine",  8,   60),
    (90,  "Clothing",    "Dresses",        "UrbanStyle:FashionX:TrendWear:ModernFit:ClassicLine", 15,  120),
    (100, "Clothing",    "Footwear",       "UrbanStyle:FashionX:TrendWear:ModernFit:ClassicLine", 20,  150),
    (115, "Home & Living","Furniture",     "HomeComfort:LivingPlus:CozySpace:UrbanNest:NestWell", 50,  300),
    (125, "Home & Living","Kitchen",       "HomeComfort:LivingPlus:CozySpace:UrbanNest:NestWell", 15,  200),
    (135, "Home & Living","Bath & Bedding","HomeComfort:LivingPlus:CozySpace:UrbanNest:NestWell", 15,  150),
    (140, "Home & Living","Decor",         "HomeComfort:LivingPlus:CozySpace:UrbanNest:NestWell", 10,  100),
    (155, "Sports",      "Fitness",        "ActivePeak:ProSport:OutdoorEdge:StrideOn:FitMax",     15,  200),
    (170, "Sports",      "Outdoor Gear",   "ActivePeak:ProSport:OutdoorEdge:StrideOn:FitMax",     20,  150),
    (185, "Food & Beverage","Coffee & Tea","NaturaBite:FreshPick:OrganicJoy:NutriBlend:TasteBest", 2,   30),
    (200, "Food & Beverage","Snacks",      "NaturaBite:FreshPick:OrganicJoy:NutriBlend:TasteBest", 2,   20),
]

_REGIONS = ["Northeast","West","Midwest","South","Southwest","Northeast","West","Mountain","South","Southeast"]
_STORE_TYPES = ["Flagship","Standard","Outlet"]
_SQ_FT_RANGES = {"Flagship":(8000,15000), "Standard":(3000,7000), "Outlet":(1500,3500)}
_JOB_TITLES   = ["Sales Associate","Senior Sales Associate","Department Manager","Assistant Manager","Store Manager","Cashier"]
_DEPARTMENTS  = ["Sales","Customer Service","Operations","Management"]
_PROMO_TYPES  = ["Seasonal","Loyalty","Flash","Clearance"]
_DISC_RANGES  = {"Seasonal":(10,25),"Loyalty":(5,15),"Flash":(20,40),"Clearance":(30,50)}
_PAYMENTS     = ["CREDIT_CARD","DEBIT_CARD","CASH","PAYPAL","APPLE_PAY"]
_SEGMENTS     = {0:"Platinum",1:"Gold",2:"Gold",3:"Silver",4:"Silver",5:"Silver"}


def _cat_for(rn):
    for threshold, cat, sub, brands, lo, hi in _CAT_MAP:
        if rn <= threshold:
            return cat, sub, brands.split(":"), lo, hi
    return "Food & Beverage", "Snacks", ["NaturaBite"], 2, 20


# ── Insert functions ──────────────────────────────────────────────────────────

def _insert_dim_date(cur):
    rows = []
    d = _START
    while d <= _END:
        iso = str(d)
        key = int(d.strftime("%Y%m%d"))
        dow = d.weekday() + 1   # 1=Mon … 7=Sun, align to Sun=0 like Snowflake
        dow_sf = (d.weekday() + 1) % 7    # 0=Sun
        week = d.isocalendar()[1]
        holiday = 1 if iso in _HOLIDAYS else 0
        weekend = 1 if dow_sf in (0, 6) else 0
        rows.append((
            key, iso, dow_sf, _DAYS[dow_sf], d.day,
            d.timetuple().tm_yday, week, d.month,
            _MONTHS[d.month - 1], (d.month - 1) // 3 + 1, d.year,
            weekend, holiday,
        ))
        d += timedelta(days=1)
    cur.executemany(
        "INSERT OR IGNORE INTO DIM_DATE VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    print(f"  DIM_DATE:      {len(rows):>6} rows")


def _insert_dim_customer(cur, rng):
    rows = []
    for rn in range(1, 501):
        fn = _pick(_FIRST, rn)
        ln = _pick(_LAST, (rn * 7 + 2) % len(_LAST))
        city = _pick(_CITIES, rn * 3 % len(_CITIES))
        state = _pick(_STATES, rn * 3 % len(_STATES))
        bd = None if rn % 20 == 0 else str(
            date(rng.randint(1953, 2005), rng.randint(1, 12), rng.randint(1, 28))
        )
        reg = str(_START - timedelta(days=rng.randint(30, 1095)))
        seg = _SEGMENTS.get(rn % 10, "Bronze")
        rows.append((
            rn, f"CUST{rn:06d}", fn, ln,
            f"{fn.lower()}.{ln.lower()}{rn}@example.com",
            ["M","F","O"][rn % 3], bd, city, state, "US", seg, reg,
        ))
    cur.executemany("INSERT OR IGNORE INTO DIM_CUSTOMER VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    print(f"  DIM_CUSTOMER:  {len(rows):>6} rows")


def _insert_dim_product(cur, rng):
    rows = []
    for rn in range(1, 201):
        cat, sub, brands, lo, hi = _cat_for(rn)
        brand = _pick(brands, rn % len(brands))
        cost = round(rng.uniform(lo, hi), 2)
        markup = rng.uniform(1.25, 1.80)
        price = round(cost * markup, 2)
        margin = round((1 - 1 / markup) * 100, 2)
        rows.append((
            rn, f"PROD{rn:06d}",
            f"{brand} {sub} #{(rn - 1) % 30 + 1:03d}",
            cat, sub, brand, cost, price, margin, 1,
        ))
    cur.executemany("INSERT OR IGNORE INTO DIM_PRODUCT VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    print(f"  DIM_PRODUCT:   {len(rows):>6} rows")


def _insert_dim_store(cur, rng):
    rows = []
    for rn in range(1, 51):
        city  = _pick(_CITIES, rn % len(_CITIES))
        state = _pick(_STATES, rn % len(_STATES))
        region = _pick(_REGIONS, rn % len(_REGIONS))
        stype = _pick(_STORE_TYPES, rn % len(_STORE_TYPES))
        lo, hi = _SQ_FT_RANGES[stype]
        open_d = str(_START - timedelta(days=rng.randint(365, 3650)))
        rows.append((
            rn, f"STR{rn:04d}", f"{stype} — {city}",
            city, state, region, stype, open_d, rng.randint(lo, hi),
        ))
    cur.executemany("INSERT OR IGNORE INTO DIM_STORE VALUES (?,?,?,?,?,?,?,?,?)", rows)
    print(f"  DIM_STORE:     {len(rows):>6} rows")


def _insert_dim_employee(cur, rng):
    rows = []
    for rn in range(1, 101):
        fn = _pick(_FIRST, rn)
        ln = _pick(_LAST, (rn * 11 + 3) % len(_LAST))
        job = _pick(_JOB_TITLES, rn % len(_JOB_TITLES))
        dept = _pick(_DEPARTMENTS, rn % len(_DEPARTMENTS))
        hire = str(_START - timedelta(days=rng.randint(30, 2190)))
        active = 0 if rn % 20 == 0 else 1
        rows.append((rn, f"EMP{rn:05d}", fn, ln, job, dept, (rn - 1) % 50 + 1, hire, active))
    cur.executemany("INSERT OR IGNORE INTO DIM_EMPLOYEE VALUES (?,?,?,?,?,?,?,?,?)", rows)
    print(f"  DIM_EMPLOYEE:  {len(rows):>6} rows")


def _insert_dim_promotion(cur, rng):
    rows = []
    for rn in range(1, 31):
        ptype = _pick(_PROMO_TYPES, rn % len(_PROMO_TYPES))
        lo, hi = _DISC_RANGES[ptype]
        disc = round(rng.uniform(lo, hi), 2)
        start = _START + timedelta(days=(rn - 1) * 24)
        end_d = start + timedelta(days=rng.randint(7, 30))
        active = 1 if rn <= 20 else 0
        rows.append((
            rn, f"PROMO{rn:05d}", f"{ptype} Event #{rn}",
            ptype, disc, str(start), str(end_d), active,
        ))
    cur.executemany("INSERT OR IGNORE INTO DIM_PROMOTION VALUES (?,?,?,?,?,?,?,?)", rows)
    print(f"  DIM_PROMOTION: {len(rows):>6} rows")


def _insert_fact_sales(cur, rng, products, promotions):
    all_dates = []
    d = _START
    while d <= _END:
        all_dates.append(int(d.strftime("%Y%m%d")))
        d += timedelta(days=1)

    rows = []
    for rn in range(1, 10_001):
        date_key    = rng.choice(all_dates)
        cust_key    = rng.randint(1, 500)
        prod_key    = rng.randint(1, 200)
        store_key   = rng.randint(1,  50)
        emp_key     = rng.randint(1, 100)
        promo_key   = rng.randint(1, 30) if rng.random() < 0.30 else None
        qty         = max(1, min(10, round(rng.gauss(2.5, 1.5))))
        payment     = _pick(_PAYMENTS, rn % len(_PAYMENTS))

        cost, price = products[prod_key - 1]
        disc_pct    = promotions[promo_key - 1] if promo_key else 0.0
        discount    = round(price * qty * disc_pct / 100, 2)
        gross       = round(price * qty, 2)
        net         = round(gross - discount, 2)
        tax         = round(net * 0.08, 2)
        margin      = round((price - cost) * qty * (1 - disc_pct / 100), 2)
        rows.append((
            rn, date_key, cust_key, prod_key, store_key, emp_key, promo_key,
            f"TXN{rn:08d}", payment, qty, price, cost,
            discount, gross, net, tax, margin,
        ))
    cur.executemany(
        "INSERT OR IGNORE INTO FACT_SALES VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    print(f"  FACT_SALES:    {len(rows):>6} rows")


# ── Main ──────────────────────────────────────────────────────────────────────

def setup(db_path: str = "./sample.db") -> None:
    """Create (or overwrite) the sample SQLite database at *db_path*."""
    rng = random.Random(_SEED)

    conn = sqlite3.connect(db_path)
    conn.executescript("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    # Create tables
    conn.executescript(_DDL)
    conn.commit()

    print(f"\nInserting sample data into {db_path} …")
    _insert_dim_date(cur)
    _insert_dim_store(cur, rng)
    _insert_dim_customer(cur, rng)
    _insert_dim_product(cur, rng)
    _insert_dim_employee(cur, rng)
    _insert_dim_promotion(cur, rng)

    # Pre-fetch product costs/prices and promo discounts for FACT_SALES
    cur.execute("SELECT UNIT_COST, UNIT_PRICE FROM DIM_PRODUCT ORDER BY PRODUCT_KEY")
    products = [(r[0], r[1]) for r in cur.fetchall()]
    cur.execute("SELECT DISCOUNT_PERCENT FROM DIM_PROMOTION ORDER BY PROMOTION_KEY")
    promotions = [r[0] for r in cur.fetchall()]

    _insert_fact_sales(cur, rng, products, promotions)
    conn.commit()
    conn.close()
    print(f"\nDone. Database ready at: {db_path}\n")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "./sample.db"
    setup(path)
