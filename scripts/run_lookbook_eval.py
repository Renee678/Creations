"""Evaluate the Lookbook on a folder of selfies and write evals/lookbook-results.md (see evals/README.md).

    python scripts/run_lookbook_eval.py                                  # evals/lookbook-photos
    python scripts/run_lookbook_eval.py --season summer --undertone cool   # also score against your known answer
    python scripts/run_lookbook_eval.py --fake                           # offline, to check the harness itself
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

from lookmate.lookbook_eval import main  # noqa: E402

if __name__ == "__main__":
    main()
