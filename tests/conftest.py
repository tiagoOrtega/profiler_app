"""Shared pytest configuration — adds project root to sys.path."""

import sys
from pathlib import Path

# Ensure all project modules are importable from the test directory
sys.path.insert(0, str(Path(__file__).parent.parent))
