"""Launch CLASS_VII using credentials stored in CLASS_VII.xlsm.

Keep CLASS_VII.xlsm beside this file. Credentials are read from its
User_Credential worksheet; no config.json is required.
"""
from pathlib import Path

from class_vii.__main__ import main

ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "CLASS_VII.xlsm"


if __name__ == "__main__":
    if not WORKBOOK.is_file():
        raise SystemExit(
            f"Workbook not found: {WORKBOOK}\n"
            "Put CLASS_VII.xlsm beside run.py."
        )

    raise SystemExit(
        main([
            "--credentials-workbook", str(WORKBOOK),
            "terminal",
            "--workbook", str(WORKBOOK),
        ])
    )
