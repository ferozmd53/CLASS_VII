"""One Shoonya login/session for the whole toolkit.

Replaces two mutually incompatible login implementations:

* repo 1 ``Shoonya_login()`` - userid/password/TOTP + vendor_code + api_secret,
  reading cells B2..B8 of the terminal workbook and printing the password.
* repo 2 ``Shoonya_login()`` + ``get_auth.py`` - an OAuth auth-code scraped
  from a Selenium browser window into cell B7 of symbols.xlsx.

Both are supported here, plus plain ``susertoken`` re-use.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from . import settings as S


class LoginError(RuntimeError):
    """Raised when Shoonya cannot be reached or refuses the credentials."""


def generate_totp(secret: str) -> str:
    """TOTP from a base32 secret, or pass through an already-computed 6-digit pin."""
    secret = str(secret or "").strip()
    if not secret:
        raise LoginError("empty TOTP secret")
    if secret.isdigit() and len(secret) == 6:
        return secret
    try:
        import pyotp
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LoginError("pyotp is not installed: pip install pyotp") from exc
    return pyotp.TOTP(secret).now()


class ShoonyaSession:
    """Owns one ``NorenApi`` instance and performs the login handshake."""

    def __init__(
        self,
        credentials: S.Credentials,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.credentials = credentials
        self.log = logger or logging.getLogger("class_vii.auth")
        self.api: Any = None
        self.client_name: str = ""
        self.token: str = ""
        self._connected = False

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, retries: int = 3, retry_delay: float = 2.0) -> Any:
        """Log in and return the ready ``NorenApi`` instance."""
        self.credentials.validate()
        method = self.credentials.login_method.upper()
        last_error: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                self.api = self._build_api()
                if method == "NEW_SESSION":
                    self._login_new_session()
                elif method == "OAUTH":
                    self._login_oauth()
                elif method == "TOKEN":
                    self._login_token()
                else:  # validated earlier, kept for safety
                    raise LoginError(f"unsupported login_method {method!r}")
                self._connected = True
                self.log.info(
                    "logged in as %s (method=%s)", self.credentials.user_id, method
                )
                return self.api
            except Exception as exc:  # noqa: BLE001 - reported, then retried
                last_error = exc
                self._connected = False
                self.log.warning(
                    "login attempt %s/%s failed: %s", attempt, retries, exc
                )
                if attempt < retries:
                    time.sleep(retry_delay * attempt)

        raise LoginError(f"login failed after {retries} attempts: {last_error}")

    def disconnect(self) -> None:
        api = self.api
        self._connected = False
        close = getattr(api, "close_websocket", None) if api else None
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                self.log.debug("websocket close failed: %s", exc)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _build_api(self) -> Any:
        try:
            from NorenRestApiPy.NorenApi import NorenApi
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise LoginError(
                "NorenRestApiPy is not installed: pip install NorenRestApiPy"
            ) from exc

        host, websocket, eod = S.API_HOST, S.API_WS, S.API_EOD

        class ShoonyaApiPy(NorenApi):  # noqa: N801 - broker naming kept
            def __init__(self) -> None:
                super().__init__(host=host, websocket=websocket, eodhost=eod)

        try:
            return ShoonyaApiPy()
        except TypeError:
            # Older NorenApi builds have no eodhost kwarg.
            class ShoonyaApiPyNoEod(NorenApi):  # noqa: N801
                def __init__(self) -> None:
                    super().__init__(host=host, websocket=websocket)

            return ShoonyaApiPyNoEod()

    def _login_new_session(self) -> None:
        creds = self.credentials
        two_fa = generate_totp(creds.totp_secret)
        response = self.api.login(
            userid=creds.user_id,
            password=str(creds.password),
            twoFA=str(two_fa),
            vendor_code=creds.vendor_code,
            api_secret=creds.api_secret,
            imei=creds.imei,
        )
        if not response or str(response.get("stat", "")).lower() != "ok":
            raise LoginError(f"login rejected: {response}")
        self.client_name = str(response.get("uname", ""))
        self.token = str(response.get("susertoken", ""))
        # The password is deliberately NOT logged: the upstream script printed
        # userid/password/api_secret straight into the console and log file.

    def _login_oauth(self) -> None:
        creds = self.credentials
        client_id = creds.vendor_code or f"{creds.user_id}_U"
        result = self.api.getAccessToken(
            creds.access_token, creds.api_secret, client_id, creds.user_id
        )
        if not result:
            raise LoginError("OAuth token exchange returned nothing")
        # NorenRestApiOAuth returns (access_token, uid, refresh_token, actid).
        access_token, uid, _refresh, actid = result
        self.api.injectOAuthHeader(access_token, uid, actid or creds.actid)
        self.token = str(access_token)
        self.client_name = str(uid)

    def _login_token(self) -> None:
        creds = self.credentials
        session = self.api.set_session(
            userid=creds.user_id,
            password=str(creds.password),
            usertoken=creds.access_token,
        )
        limits = self.api.get_limits()
        if not limits or str(limits.get("stat", "")).lower() != "ok":
            detail = (limits or {}).get("emsg", session)
            raise LoginError(
                f"stored token rejected ({detail}); log in with NEW_SESSION "
                "and refresh the token"
            )
        self.token = str(creds.access_token)
        self.client_name = str((session or {}).get("uname", ""))
