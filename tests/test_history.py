"""Tests for history.py — HistoryManager persistence and retrieval."""

import json
import os
import tempfile

import pytest

from history import HistoryManager
from profiling import ColumnProfile, TableProfile


def _make_profile(row_count=100, col_std_dev=5.0, col_null_rate=0.0):
    col = ColumnProfile(
        name="amount", data_type="NUMBER",
        row_count=row_count, null_count=int(row_count * col_null_rate),
        null_rate=col_null_rate, distinct_count=50, uniqueness_rate=0.5,
        mean=50.0, std_dev=col_std_dev, variance=col_std_dev ** 2,
        min_val=1.0, max_val=100.0,
    )
    return TableProfile(
        database="DB", schema="SC", table="ORDERS",
        row_count=row_count, column_count=1, columns=[col],
    )


class TestHistoryManagerInit:
    def test_creates_empty_when_no_file(self):
        hm = HistoryManager("/nonexistent/history.json")
        assert hm._data == {}

    def test_loads_existing_file(self):
        data = {"DB.SC.T": {"row_count": 42, "profiled_at": "2024-01-01", "columns": {}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f); path = f.name
        try:
            hm = HistoryManager(path)
            assert hm._data == data
        finally:
            os.unlink(path)


class TestHistoryManagerKey:
    def test_key_uppercased(self):
        hm = HistoryManager("/nonexistent")
        assert hm._key("mydb", "myschema", "mytable") == "MYDB.MYSCHEMA.MYTABLE"

    def test_key_already_upper(self):
        hm = HistoryManager("/nonexistent")
        assert hm._key("DB", "SC", "T") == "DB.SC.T"


class TestHistoryManagerGetHistory:
    def test_returns_none_when_missing(self):
        hm = HistoryManager("/nonexistent")
        assert hm.get_history("DB", "SC", "ORDERS") is None

    def test_returns_entry_when_present(self):
        hm = HistoryManager("/nonexistent")
        hm._data["DB.SC.ORDERS"] = {"row_count": 99}
        assert hm.get_history("db", "sc", "orders")["row_count"] == 99

    def test_case_insensitive_lookup(self):
        hm = HistoryManager("/nonexistent")
        hm._data["DB.SC.ORDERS"] = {"row_count": 1}
        assert hm.get_history("Db", "Sc", "Orders") is not None


class TestHistoryManagerSaveProfile:
    def test_persists_to_disk(self, tmp_path):
        path = str(tmp_path / "history.json")   # file does NOT exist yet
        hm = HistoryManager(path)
        hm.save_profile(_make_profile(row_count=200))

        with open(path) as f:
            on_disk = json.load(f)
        assert "DB.SC.ORDERS" in on_disk
        assert on_disk["DB.SC.ORDERS"]["row_count"] == 200

    def test_saves_column_stats(self, tmp_path):
        path = str(tmp_path / "history.json")
        hm = HistoryManager(path)
        hm.save_profile(_make_profile(col_std_dev=7.5))
        entry = hm.get_history("DB", "SC", "ORDERS")
        assert entry["columns"]["amount"]["std_dev"] == 7.5
        assert entry["columns"]["amount"]["min_val"] == 1.0

    def test_overwrites_previous_entry(self, tmp_path):
        path = str(tmp_path / "history.json")
        hm = HistoryManager(path)
        hm.save_profile(_make_profile(row_count=100))
        hm.save_profile(_make_profile(row_count=999))
        assert hm.get_history("DB", "SC", "ORDERS")["row_count"] == 999

    def test_roundtrip_get_after_save(self, tmp_path):
        path = str(tmp_path / "history.json")
        hm = HistoryManager(path)
        hm.save_profile(_make_profile())
        assert hm.get_history("db", "sc", "orders") is not None

    def test_loads_empty_file_as_empty_dict(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("")    # empty file — must not crash
        hm = HistoryManager(str(path))
        assert hm._data == {}
