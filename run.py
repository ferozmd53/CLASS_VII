"""Start the CLASS_VII Excel trade terminal.

Keep config.json and Finvasia_Trade_Terminal_v3.xlsm beside this file.
Edit WORKBOOK below if your workbook has a different filename.
"""
from pathlib import Path

from class_vii.__main__ import main

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
WORKBOOK = ROOT / "Finvasia_Trade_Terminal_v3.xlsm"


if __name__ == "__main__":
    if not CONFIG.exists():
        raise SystemExit(
            f"Missing {CONFIG}. Download config/config.example.json from the "
            "CLASS_VII GitHub repository, save it here as config.json, "
            "then enter your Shoonya credentials."
        )
    if not WORKBOOK.exists():
        raise SystemExit(
            f"Workbook not found: {WORKBOOK}\n"
            "Put Finvasia_Trade_Terminal_v3.xlsm beside run.py, or edit "
            "the WORKBOOK setting in run.py."
        )

    raise SystemExit(
        main([
            "--config", str(CONFIG),
            "terminal",
            "--workbook", str(WORKBOOK),
        ])
    )
