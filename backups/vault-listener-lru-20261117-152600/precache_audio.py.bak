#!/usr/bin/env python3
"""Convenience runner for Afterhours audio pre-caching."""

import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.scripts.precache_audio import main

if __name__ == "__main__":
    main()
