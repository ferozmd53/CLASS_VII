"""Launch CLASS_VII using credentials stored in CLASS_VII.xlsm.

Keep CLASS_VII.xlsm beside this file. Credentials are read from its
User_Credential worksheet; no config.json is required.
"""
from pathlib import Path
from urllib.request import Request, urlopen

from openpyxl import load_workbook
from class_vii.__main__ import main

ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "CLASS_VII.xlsm"


def print_ip_addresses(workbook_path: Path) -> None:
    """Print the workbook's saved public IP and the current internet-facing IP."""
    print("=" * 60)
    print("CLASS_VII NETWORK / IP CHECK")
    print("=" * 60)

    saved_ip = ""
    book = load_workbook(workbook_path, read_only=True, data_only=True, keep_vba=True)
    try:
        if "User_Credential" in book.sheetnames:
            saved_ip = str(book["User_Credential"]["B11"].value or "").strip()
    finally:
        book.close()

    print(f"Excel Public IP (User_Credential!B11): {saved_ip or 'NOT ENTERED'}")

    try:
        request = Request(
            "https://api.ipify.org",
            headers={"User-Agent": "CLASS_VII/1.0"},
        )
        with urlopen(request, timeout=8) as response:
            current_ip = response.read().decode("utf-8").strip()
        print(f"Current Internet Public IP:           {current_ip or 'NOT AVAILABLE'}")
        if saved_ip and current_ip:
            if saved_ip == current_ip:
                print("IP CHECK: MATCH")
            else:
                print("IP CHECK: MISMATCH - verify the IP whitelisted with Shoonya.")
    except Exception as exc:
        print(f"Current Internet Public IP:           COULD NOT DETERMINE ({exc})")
    print("=" * 60, flush=True)


if __name__ == "__main__":
    if not WORKBOOK.is_file():
        raise SystemExit(
            f"Workbook not found: {WORKBOOK}\n"
            "Put CLASS_VII.xlsm beside run.py."
        )

    print_ip_addresses(WORKBOOK)

    raise SystemExit(
        main([
            "--credentials-workbook", str(WORKBOOK),
            "terminal",
            "--workbook", str(WORKBOOK),
        ])
    )
