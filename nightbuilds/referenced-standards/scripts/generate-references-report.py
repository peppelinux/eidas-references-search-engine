#!/usr/bin/env python3
"""Deprecated path — reports are generated from the corpus root."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_CORPUS_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate-references-report.py"

if __name__ == "__main__":
    sys.argv[0] = str(_CORPUS_SCRIPT)
    runpy.run_path(str(_CORPUS_SCRIPT), run_name="__main__")
