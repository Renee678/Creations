"""Evaluate the AI on evals/cases.csv and write evals/results.md (see evals/README.md).

    python scripts/run_eval.py                 # Claude if ANTHROPIC_API_KEY is in .env, else the offline model
    python scripts/run_eval.py --fake          # offline, to check the harness itself
    python scripts/run_eval.py --tryon --person evals/photos/me.jpg
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

from lookmate.eval import main  # noqa: E402

if __name__ == "__main__":
    main()
