"""Persist profiling results to JSON for historical drift detection."""

import json
import os
from typing import Optional

from profiling import TableProfile


class HistoryManager:
    def __init__(self, history_file: str):
        self.history_file = history_file
        self._data: dict = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError):
                return {}   # empty or corrupted file — start fresh
        return {}

    def _save(self) -> None:
        with open(self.history_file, "w") as f:
            json.dump(self._data, f, indent=2, default=str)

    def _key(self, database: str, schema: str, table: str) -> str:
        return f"{database}.{schema}.{table}".upper()

    def get_history(self, database: str, schema: str, table: str) -> Optional[dict]:
        return self._data.get(self._key(database, schema, table))

    def save_profile(self, profile: TableProfile) -> None:
        self._data[self._key(profile.database, profile.schema, profile.table)] = {
            "row_count": profile.row_count,
            "profiled_at": profile.profiled_at,
            "columns": {
                col.name: {
                    "null_rate": col.null_rate,
                    "std_dev": col.std_dev,
                    "variance": col.variance,
                    "mean": col.mean,
                    "min_val": col.min_val,
                    "max_val": col.max_val,
                }
                for col in profile.columns
            },
        }
        self._save()
