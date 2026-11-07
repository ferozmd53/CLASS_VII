"""Shoonya OAuth authentication for the CLASS_VII terminal.

Shoonya's current login flow is OAuth: open the hosted login page, capture a
fresh one-time authorization code, exchange it for a session token, and inject
that token into the Noren API client. Workbook credentials are read in memory
only; secrets and tokens are never printed or committed.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlsplit

from . import settings as S


class LoginError(RuntimeError):
    """Raised when Shoonya cannot be reached or refuses authentication."""


def generate_totp(secret: str) -> str:
    """Generate the current 6-digit TOTP from the workbook's base32 secret."""
    secret = str(secret or "").strip()
    if not secret:
        raise LoginError("TOTP secret is empty")
    try:
        import pyotp
    except ImportError as exc:
        raise LoginError("pyotp is missing; run the updated install command") from exc
    try:
        return pyotp.TOTP(secret).now()
    except Exception as exc:
        raise LoginError("TOTP secret is invalid; check User_Credential!B5") from exc


def _authorization_code_from_url(url: str) -> str:
    """Extract code from either a query-string or hash-style redirect."""
    parts = urlsplit(url)
    candidates = [parts.query, parts.fragment]
    for candidate in candidates:
        query = candidate.split("?", 1)[-1]
        values = parse_qs(query)
        code = values.get("code", [""])[0].strip()
        if code:
            return code
    return ""


class ShoonyaSession:
    """Owns one NorenApi instance and performs a fresh OAuth login."""

    def __init__(
        self,
        credentials: S.Credentials,
        logger: Optional[logging.Logger] = None,
        auth_code_callback=None,
    ) -> None:
        self.credentials = credentials
        self.auth_code_callback = auth_code_callback
        self.log = logger or logging.getLogger("class_vii.auth")
        self.api: Any = None
        self.client_name = ""
        self.token = ""
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, retries: int = 1, retry_delay: float = 2.0) -> Any:
        """Authenticate once; a consumed OAuth code must never be retried."""
        self.credentials.validate()
        method = self.credentials.login_method.upper()
        if method not in {"NEW_SESSION", "OAUTH"}:
            raise LoginError(
                "Only fresh OAuth login is enabled. Set User_Credential!B4 to New_Session; "
                "saved session tokens are cleared and never reused."
            )

        last_error: Optional[Exception] = None
        for attempt in range(1, max(1, retries) + 1):
            try:
                self.api = self._build_api()
                self._login_oauth()
                self._connected = True
                self.log.info("Shoonya OAuth session established for %s", self.credentials.user_id)
                return self.api
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self._connected = False
                self.log.error("Shoonya login failed: %s", exc)
                if attempt < retries:
                    time.sleep(retry_delay * attempt)
        raise LoginError(f"Shoonya login failed: {last_error}")

    def disconnect(self) -> None:
        api = self.api
        self._connected = False
        logout = getattr(api, "logout", None) if api else None
        if callable(logout):
            try:
                logout()
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Shoonya logout failed: %s", exc)

    def _build_api(self) -> Any:
        try:
            from NorenRestApiPy.NorenApi import NorenApi
        except ImportError as exc:
            raise LoginError(
                "OAuth SDK is missing. Run: py -3.14 -m pip install --upgrade "
                "NorenRestApiOAuth"
            ) from exc

        host, websocket, eod = S.API_HOST, S.API_WS, S.API_EOD

        class ShoonyaApiPy(NorenApi):  # noqa: N801
            def __init__(self) -> None:
                try:
                    super().__init__(host=host, websocket=websocket, eodhost=eod)
                except TypeError:
                    super().__init__(host=host, websocket=websocket)

        return ShoonyaApiPy()

    def _capture_auth_code(self) -> str:
        """Open Shoonya's hosted login in Chrome and capture its one-time code."""
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
        except ImportError as exc:
            raise LoginError(
                "Browser login packages are missing. Run the updated install command."
            ) from exc

        creds = self.credentials
        client_id = creds.vendor_code.strip()
        if not client_id:
            raise LoginError("Client ID is missing in User_Credential!B6")

        login_url = (
            "https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id="
            + quote(client_id, safe="")
        )
        driver = None
        try:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
            driver.maximize_window()
            driver.get(login_url)

            deadline = time.time() + 45
            visible_inputs = []
            while time.time() < deadline:
                visible_inputs = [
                    item for item in driver.find_elements(By.CSS_SELECTOR, "input")
                    if item.is_displayed() and item.is_enabled()
                    and (item.get_attribute("type") or "").lower() != "hidden"
                ]
                if len(visible_inputs) >= 3:
                    break
                time.sleep(0.5)
            if len(visible_inputs) < 3:
                raise LoginError(
                    "Shoonya login form did not load. Check Chrome, internet and the OAuth Client ID."
                )

            visible_inputs[0].send_keys(creds.user_id)
            visible_inputs[1].send_keys(creds.password)
            visible_inputs[2].send_keys(generate_totp(creds.totp_secret))

            clicked = False
            for button in driver.find_elements(By.TAG_NAME, "button"):
                if button.is_displayed() and "LOGIN" in (button.text or "").upper():
                    driver.execute_script("arguments[0].click();", button)
                    clicked = True
                    break
            if not clicked:
                raise LoginError("Could not find the LOGIN button on Shoonya's page")

            deadline = time.time() + 90
            while time.time() < deadline:
                code = _authorization_code_from_url(driver.current_url)
                if code:
                    return code
                time.sleep(0.25)
            raise LoginError(
                "Timed out waiting for Shoonya's OAuth redirect. Complete any extra "
                "verification in the Chrome window and try again."
            )
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _login_oauth(self) -> None:
        creds = self.credentials
        # Authorization codes are short-lived and single-use. Always obtain a
        # fresh code; never reuse the old B10 value from a previous run.
        auth_code = self._capture_auth_code()
        if self.auth_code_callback is not None:
            try:
                self.auth_code_callback(auth_code)
                self.log.info("Fresh one-time AUTH_CODE saved to User_Credential!B10 (not reused)")
            except Exception as exc:
                raise LoginError("Could not save the fresh AUTH_CODE to User_Credential!B10") from exc
        try:
            result = self.api.getAccessToken(
                auth_code,
                creds.api_secret,
                creds.vendor_code,
                creds.user_id,
            )
        except Exception as exc:
            raise LoginError(
                "OAuth code exchange failed. Verify the Client ID in B6, API Secret "
                "in B7 and that the Client ID IP whitelist is current."
            ) from exc

        if not result or not isinstance(result, (tuple, list)) or len(result) < 4:
            raise LoginError("Shoonya returned an invalid OAuth token response")

        access_token, uid, _refresh_token, actid = result[:4]
        if not access_token or not uid:
            raise LoginError("Shoonya OAuth response did not contain an access token")
        self.api.injectOAuthHeader(access_token, uid, actid or creds.actid)
        self.token = str(access_token)
        self.client_name = str(uid)

    def _login_token(self) -> None:
        creds = self.credentials
        if not creds.access_token:
            raise LoginError("Existing session token is empty in User_Credential!B8")
        try:
            self.api.set_session(
                userid=creds.user_id,
                password="",
                usertoken=creds.access_token,
            )
            response = self.api.get_limits()
        except Exception as exc:
            raise LoginError("Existing session token was rejected or expired") from exc
        if not response or str(response.get("stat", "")).lower() != "ok":
            raise LoginError("Existing session token was rejected or expired")
        self.token = creds.access_token
        self.client_name = creds.user_id
