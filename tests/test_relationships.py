"""Tests for relationships.py — RelationshipDetector."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from relationships import RelationshipDetector, RelationshipResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_profile(dir_path: Path, db, schema, table, columns):
    key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
    profile = {
        "database": db, "schema": schema, "table": table,
        "row_count": 100, "column_count": len(columns),
        "columns": [
            {"name": c, "distinct_count": 50, "null_rate": 0.0,
             "null_count": 0, "row_count": 100, "uniqueness_rate": 0.5}
            for c in columns
        ],
    }
    (dir_path / f"{key}.json").write_text(json.dumps(profile))


def _mock_conn(src_d=5, tgt_d=5, orphans=0):
    conn = MagicMock()
    conn.fetch_one.return_value = (src_d, tgt_d, orphans)
    return conn


# ── _load_profiles ────────────────────────────────────────────────────────────

class TestLoadProfiles:
    def test_empty_directory(self, tmp_path):
        d = RelationshipDetector(_mock_conn(), tmp_path)
        assert d._load_profiles() == []

    def test_loads_valid_profiles(self, tmp_path):
        _write_profile(tmp_path, "DB", "SC", "T1", ["id"])
        _write_profile(tmp_path, "DB", "SC", "T2", ["id"])
        d = RelationshipDetector(_mock_conn(), tmp_path)
        profiles = d._load_profiles()
        assert len(profiles) == 2

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("not json {{{")
        _write_profile(tmp_path, "DB", "SC", "T1", ["id"])
        d = RelationshipDetector(_mock_conn(), tmp_path)
        profiles = d._load_profiles()
        assert len(profiles) == 1   # bad file skipped


# ── _candidates ───────────────────────────────────────────────────────────────

class TestCandidates:
    def _profiles(self, tmp_path):
        _write_profile(tmp_path, "DB", "SC", "FACT", ["date_key", "amount"])
        _write_profile(tmp_path, "DB", "SC", "DIM",  ["date_key", "full_date"])
        d = RelationshipDetector(_mock_conn(), tmp_path)
        return d._load_profiles(), d

    def test_excludes_source_table(self, tmp_path):
        profiles, d = self._profiles(tmp_path)
        cands = d._candidates("date_key", "DB__SC__FACT", profiles)
        tables = [c["table"] for c in cands]
        assert "FACT" not in tables

    def test_finds_same_named_column(self, tmp_path):
        profiles, d = self._profiles(tmp_path)
        cands = d._candidates("date_key", "DB__SC__FACT", profiles)
        assert len(cands) == 1
        assert cands[0]["table"] == "DIM"

    def test_case_insensitive_match(self, tmp_path):
        profiles, d = self._profiles(tmp_path)
        cands = d._candidates("DATE_KEY", "DB__SC__FACT", profiles)
        assert len(cands) == 1

    def test_no_match_for_unknown_column(self, tmp_path):
        profiles, d = self._profiles(tmp_path)
        cands = d._candidates("unknown_col", "DB__SC__FACT", profiles)
        assert cands == []


# ── _test ─────────────────────────────────────────────────────────────────────

class TestTest:
    def _run(self, tmp_path, src_d=10, tgt_d=10, orphans=0):
        conn = _mock_conn(src_d, tgt_d, orphans)
        d = RelationshipDetector(conn, tmp_path)
        return d._test("DB", "SC", "FACT", "date_key",
                       "DB", "SC", "DIM",  "date_key")

    def test_pass_when_no_orphans(self, tmp_path):
        r = self._run(tmp_path, src_d=10, tgt_d=10, orphans=0)
        assert r.status == "PASS"
        assert r.matched == 10
        assert r.orphans == 0
        assert r.match_pct == pytest.approx(1.0)

    def test_warn_when_mostly_matched(self, tmp_path):
        r = self._run(tmp_path, src_d=10, tgt_d=10, orphans=1)
        assert r.status == "WARN"    # 90% match

    def test_fail_when_poor_match(self, tmp_path):
        r = self._run(tmp_path, src_d=10, tgt_d=10, orphans=5)
        assert r.status == "FAIL"

    def test_zero_src_distinct_gives_zero_match_pct(self, tmp_path):
        conn = _mock_conn(0, 0, 0)
        d = RelationshipDetector(conn, tmp_path)
        r = d._test("DB", "SC", "F", "c", "DB", "SC", "T", "c")
        assert r.match_pct == 0.0

    def test_uses_platform_table_ref_when_available(self, tmp_path):
        conn = MagicMock()
        conn.fetch_one.return_value = (5, 5, 0)
        conn.table_ref.side_effect = lambda db, sc, tb: f"`{db}`.`{sc}`.`{tb}`"
        conn.quote_col = lambda n: f"`{n}`"
        d = RelationshipDetector(conn, tmp_path)
        d._test("DB","SC","FACT","col","DB","SC","DIM","col")
        sql = conn.fetch_one.call_args[0][0]
        assert "`DB`" in sql   # platform table_ref was used

    def test_falls_back_to_ansi_quoting_without_table_ref(self, tmp_path):
        conn = MagicMock(spec=["fetch_one", "fetch_all"])
        conn.fetch_one.return_value = (5, 5, 0)
        d = RelationshipDetector(conn, tmp_path)
        d._test("DB","SC","FACT","col","DB","SC","DIM","col")
        sql = conn.fetch_one.call_args[0][0]
        assert '"DB"' in sql   # ANSI double-quote fallback


# ── detect ────────────────────────────────────────────────────────────────────

class TestDetect:
    def test_returns_empty_when_no_profile_found(self, tmp_path):
        d = RelationshipDetector(_mock_conn(), tmp_path)
        assert d.detect("DB", "SC", "NONEXISTENT") == []

    def test_returns_empty_when_column_distinct_zero(self, tmp_path):
        profile = {
            "database": "DB", "schema": "SC", "table": "T",
            "row_count": 0, "column_count": 1,
            "columns": [{"name": "id", "distinct_count": 0,
                         "null_rate": 0, "null_count": 0, "row_count": 0,
                         "uniqueness_rate": 0}],
        }
        (tmp_path / "DB__SC__T.json").write_text(json.dumps(profile))
        d = RelationshipDetector(_mock_conn(), tmp_path)
        results = d.detect("DB", "SC", "T")
        assert results == []

    def test_returns_results_for_matching_columns(self, tmp_path):
        _write_profile(tmp_path, "DB", "SC", "FACT", ["date_key"])
        _write_profile(tmp_path, "DB", "SC", "DIM",  ["date_key"])
        conn = _mock_conn(5, 5, 0)
        d = RelationshipDetector(conn, tmp_path)
        results = d.detect("DB", "SC", "FACT")
        assert len(results) == 1
        assert results[0].status == "PASS"

    def test_skips_column_with_no_matches(self, tmp_path):
        _write_profile(tmp_path, "DB", "SC", "FACT", ["amount"])   # no match in DIM
        _write_profile(tmp_path, "DB", "SC", "DIM",  ["date_key"])
        d = RelationshipDetector(_mock_conn(), tmp_path)
        results = d.detect("DB", "SC", "FACT")
        assert results == []
