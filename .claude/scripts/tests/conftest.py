"""Shared pytest fixtures. Makes shared.py importable without installing as a package."""
import sys
from pathlib import Path

# Ensure .claude/scripts is on sys.path so tests can import shared, memory_flush, etc.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
