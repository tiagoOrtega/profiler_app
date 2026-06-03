"""Tests for setup_sqlite.py — SQLite sample database creation."""

import sqlite3

import pytest

import setup_sqlite


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "test.db")
    setup_sqlite.setup(path)
    return path


class TestSetupCreatesAllTables:
    EXPECTED_TABLES = {
        "DIM_DATE", "DIM_CUSTOMER", "DIM_PRODUCT",
        "DIM_STORE", "DIM_EMPLOYEE", "DIM_PROMOTION", "FACT_SALES",
    }

    def test_all_tables_exist(self, db):
        conn = sqlite3.connect(db)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0].upper() for r in cur.fetchall()}
        conn.close()
        assert self.EXPECTED_TABLES.issubset(tables)


class TestRowCounts:
    def _count(self, db, table):
        conn = sqlite3.connect(db)
        r = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return r

    def test_dim_date_has_731_rows(self, db):
        # 2023-01-01 → 2024-12-31 inclusive: 365 (2023) + 366 (2024 leap) = 731
        assert self._count(db, "DIM_DATE") == 731

    def test_dim_customer_has_500_rows(self, db):
        assert self._count(db, "DIM_CUSTOMER") == 500

    def test_dim_product_has_200_rows(self, db):
        assert self._count(db, "DIM_PRODUCT") == 200

    def test_dim_store_has_50_rows(self, db):
        assert self._count(db, "DIM_STORE") == 50

    def test_dim_employee_has_100_rows(self, db):
        assert self._count(db, "DIM_EMPLOYEE") == 100

    def test_dim_promotion_has_30_rows(self, db):
        assert self._count(db, "DIM_PROMOTION") == 30

    def test_fact_sales_has_10000_rows(self, db):
        assert self._count(db, "FACT_SALES") == 10_000


class TestDataIntegrity:
    def test_fact_sales_product_keys_valid(self, db):
        conn = sqlite3.connect(db)
        orphans = conn.execute("""
            SELECT COUNT(*) FROM FACT_SALES f
            WHERE NOT EXISTS (
                SELECT 1 FROM DIM_PRODUCT p WHERE p.PRODUCT_KEY = f.PRODUCT_KEY
            )
        """).fetchone()[0]
        conn.close()
        assert orphans == 0

    def test_fact_sales_store_keys_valid(self, db):
        conn = sqlite3.connect(db)
        orphans = conn.execute("""
            SELECT COUNT(*) FROM FACT_SALES f
            WHERE NOT EXISTS (
                SELECT 1 FROM DIM_STORE s WHERE s.STORE_KEY = f.STORE_KEY
            )
        """).fetchone()[0]
        conn.close()
        assert orphans == 0

    def test_fact_sales_customer_keys_valid(self, db):
        conn = sqlite3.connect(db)
        orphans = conn.execute("""
            SELECT COUNT(*) FROM FACT_SALES f
            WHERE NOT EXISTS (
                SELECT 1 FROM DIM_CUSTOMER c WHERE c.CUSTOMER_KEY = f.CUSTOMER_KEY
            )
        """).fetchone()[0]
        conn.close()
        assert orphans == 0

    def test_dim_customer_has_nulls_in_birth_date(self, db):
        conn = sqlite3.connect(db)
        nulls = conn.execute(
            "SELECT COUNT(*) FROM DIM_CUSTOMER WHERE BIRTH_DATE IS NULL"
        ).fetchone()[0]
        conn.close()
        assert nulls > 0   # intentional ~5% nulls

    def test_transaction_ids_unique(self, db):
        conn = sqlite3.connect(db)
        total = conn.execute("SELECT COUNT(*) FROM FACT_SALES").fetchone()[0]
        unique = conn.execute("SELECT COUNT(DISTINCT TRANSACTION_ID) FROM FACT_SALES").fetchone()[0]
        conn.close()
        assert total == unique

    def test_segment_distribution(self, db):
        conn = sqlite3.connect(db)
        segs = {r[0] for r in conn.execute(
            "SELECT DISTINCT CUSTOMER_SEGMENT FROM DIM_CUSTOMER"
        ).fetchall()}
        conn.close()
        assert "Bronze" in segs
        assert "Platinum" in segs

    def test_idempotent_rerun(self, tmp_path):
        path = str(tmp_path / "rerun.db")
        setup_sqlite.setup(path)
        setup_sqlite.setup(path)   # second run must not fail
        conn = sqlite3.connect(path)
        count = conn.execute("SELECT COUNT(*) FROM FACT_SALES").fetchone()[0]
        conn.close()
        assert count == 10_000    # no duplicate rows (INSERT OR IGNORE)


class TestHelpers:
    def test_rand_date_in_range(self):
        from datetime import date
        import random
        rng = random.Random(0)
        d = setup_sqlite._rand_date(rng)
        assert "2023" in d or "2024" in d

    def test_pick_wraps_around(self):
        lst = ["a", "b", "c"]
        assert setup_sqlite._pick(lst, 3) == "a"
        assert setup_sqlite._pick(lst, 4) == "b"

    def test_cat_for_electronics(self):
        cat, sub, brands, lo, hi = setup_sqlite._cat_for(1)
        assert cat == "Electronics"
        assert lo > 0 and hi > lo

    def test_cat_for_food(self):
        cat, sub, brands, lo, hi = setup_sqlite._cat_for(200)
        assert cat == "Food & Beverage"
