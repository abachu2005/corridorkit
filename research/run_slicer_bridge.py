#!/usr/bin/env python3
"""Run the headless Slicer JSON bridge from an unpacked source checkout."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skullbase_corridor.application.slicer_bridge import main


if __name__ == "__main__":
    raise SystemExit(main())
