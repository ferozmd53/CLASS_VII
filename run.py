"""Visible Excel launcher for CLASS_VII.

At every startup it clears the old session token (B8) and old one-time
authorization code (B10), updates the public IP in B11, then starts a fresh
Shoonya OAuth login. The new AUTH_CODE is written to B10 for reference only;
it is never read or reused. Access tokens stay in memory only.
"""
from pathlib import Path
from urllib.request import Request, urlopen

import xlwings as xw

from class_vii.__main__ import main

ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "CLASS_VII.xlsm"


def get_public_ip() -> str:
    request = Request("https://api.ipify.org", headers={"User-Agent": "CLASS_VII/1.0"})
    with urlopen(request, timeout=10) as response:
        return response.read().decode("utf-8").strip()


def launch() -> int:
    if not WORKBOOK.is_file():
        raise SystemExit(
            f"Workbook not found: {WORKBOOK}\n"
            "Put CLASS_VII.xlsm beside run.py."
        )

    app = None
    book = None
    try:
        # Open the user's actual workbook in the desktop Excel application.
        app = xw.App(visible=True, add_book=False)
        app.display_alerts = False
        book = app.books.open(str(WORKBOOK), update_links=False, read_only=False)
        app.visible = True
        book.activate()

        if "User_Credential" not in [sheet.name for sheet in book.sheets]:
            raise RuntimeError("Workbook is missing the User_Credential worksheet")

        sheet = book.sheets["User_Credential"]

        # Never allow a previous session token or authorization code to be reused.
        sheet.range("B8").value = ""
        sheet.range("B10").value = ""

        print("=" * 64)
        print("CLASS_VII STARTUP")
        print("=" * 64)
        print("Old session token (B8): CLEARED")
        print("Old AUTH_CODE (B10): CLEARED")

        try:
            current_ip = get_public_ip()
            sheet.range("B11").value = current_ip
            print(f"Current public IP: {current_ip}")
            print("Saved to User_Credential!B11")
            print("Shoonya API whitelist: compare this IP with your registered IP.")
        except Exception as exc:
            current_ip = ""
            print(f"Current public IP: NOT AVAILABLE ({exc})")
            print("B11 was not overwritten because the current IP could not be checked.")

        book.save()

        def save_fresh_auth_code(code: str) -> None:
            # Record this run's one-time code, but never read it back for login.
            sheet.range("B10").value = code
            book.save()
            print("Fresh AUTH_CODE: saved to User_Credential!B10 (not reused)")

        print("=" * 64, flush=True)
        return main(
            [
                "--credentials-workbook", str(WORKBOOK),
                "terminal",
                "--workbook", str(WORKBOOK),
            ],
            auth_code_callback=save_fresh_auth_code,
        )
    finally:
        # The terminal may already have closed the workbook through ExcelBridge.
        try:
            if book is not None:
                book.save()
                book.close()
        except Exception:
            pass
        try:
            if app is not None:
                app.quit()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(launch())
