from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.intelligence_safety import apply_safety_patch

apply_safety_patch()

from src.intelligence_policy import apply_policy_patch

apply_policy_patch()

from src.intelligence_pipeline import main


if __name__ == "__main__":
    raise SystemExit(main())
