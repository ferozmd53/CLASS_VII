"""Central configuration for class_vii.

Every credential and tunable lives in ``config/config.json`` (git-ignored).
``config/config.example.json`` is the template that ships with the project.
Environment variables (prefix ``CLASS_VII_``) override the file, so secrets
never have to touch the disk.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DEFAULT_CONFIG_FILE = CONFIG_DIR / "config.json"

# Shoonya (Finvasia) endpoints - defined exactly once for the whole package.
API_HOST = "https://api.shoonya.com/NorenWClientAPI/"
API_WS = "wss://api.shoonya.com/NorenWSAPI/"
API_EOD = "https://api.shoonya.com/chartapi/getdata/"
INSTRUMENT_BASE = "https://api.shoonya.com/"

ENV_PREFIX = "CLASS_VII_"


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
@dataclass
class Credentials:
    """Shoonya login material. Never commit a filled-in copy of this."""

    user_id: str = ""
    password: str = ""
    totp_secret: str = ""       # base32 TOTP secret, or a 6-digit pin
    vendor_code: str = ""       # e.g. FA12345_U
    api_secret: str = ""
    imei: str = "abcd1234"
    login_method: str = "NEW_SESSION"   # NEW_SESSION | OAUTH | TOKEN
    access_token: str = ""      # OAuth auth-code (OAUTH) or susertoken (TOKEN)
    account_id: str = ""        # actid; falls back to user_id

    def missing(self) -> List[str]:
        method = (self.login_method or "").upper()
        required = ["user_id"]
        if method == "NEW_SESSION":
            required += ["password", "totp_secret", "vendor_code", "api_secret"]
        elif method == "OAUTH":
            required += ["password", "totp_secret", "vendor_code", "api_secret"]
        elif method == "TOKEN":
            required += ["access_token"]
        else:
            raise ValueError(
                "login_method must be NEW_SESSION, OAUTH or TOKEN "
                f"(got {self.login_method!r})"
            )
        return [name for name in required if not str(getattr(self, name) or "").strip()]

    def validate(self) -> None:
        missing = self.missing()
        if missing:
            raise ValueError(
                "missing credential field(s): " + ", ".join(missing)
                + " -- fill config/config.json or set the CLASS_VII_* env vars"
            )

    @property
    def actid(self) -> str:
        return self.account_id or self.user_id


# --------------------------------------------------------------------------- #
# Trading
# --------------------------------------------------------------------------- #
@dataclass
class TradingConfig:
    product_type: str = "C"          # C = delivery, I = intraday (MIS), M = NRML
    quantity: int = 1
    order_type: str = "LIMIT"        # MARKET | LIMIT | SL-M
    price_buffer_pct: float = 1.0    # price padding for SL-M (was Market_Safety)
    tick_size: float = 0.05
    target_pct: float = 1.0
    stop_loss_pct: float = 0.5
    trail_enabled: bool = True
    trail_step_pct: float = 0.2
    paper_trade: bool = True         # SAFE default: no real orders until you flip it
    poll_interval: float = 1.0


# --------------------------------------------------------------------------- #
# Scanner / indicators
# --------------------------------------------------------------------------- #
@dataclass
class ScannerConfig:
    exchange: str = "NSE"
    symbols: List[str] = field(default_factory=list)
    load_days: int = 500
    rsi_length: int = 14
    stoch_length: int = 14
    stoch_upper: float = 70.0
    stoch_lower: float = 30.0
    bb_length: int = 20
    bb_std: float = 2.0
    sma_stoch_length: int = 3
    excel_update_interval: float = 0.5
    workbook: str = "class_vii_scanner.xlsx"


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
@dataclass
class AlertConfig:
    telegram_token: str = ""
    telegram_chat_id: str = ""
    voice: bool = False              # pyttsx3 needs a desktop session
    log_dir: str = "logs"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


# --------------------------------------------------------------------------- #
# Root config object
# --------------------------------------------------------------------------- #
@dataclass
class AppConfig:
    credentials: Credentials = field(default_factory=Credentials)
    trading: TradingConfig = field(default_factory=TradingConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    terminal_workbook: str = "Finvasia_Trade_Terminal_v3.xlsm"

    # -- loading ---------------------------------------------------------- #
    @classmethod
    def load(cls, path: Optional[os.PathLike] = None) -> "AppConfig":
        path = Path(path or os.getenv(ENV_PREFIX + "CONFIG") or DEFAULT_CONFIG_FILE)
        raw: Dict[str, Any] = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle) or {}

        config = cls(
            credentials=Credentials(**raw.get("credentials", {})),
            trading=TradingConfig(**raw.get("trading", {})),
            scanner=ScannerConfig(**raw.get("scanner", {})),
            alerts=AlertConfig(**raw.get("alerts", {})),
            terminal_workbook=raw.get(
                "terminal_workbook", "Finvasia_Trade_Terminal_v3.xlsm"
            ),
        )
        config._apply_env()
        return config

    def _apply_env(self) -> None:
        """CLASS_VII_USER_ID / CLASS_VII_PASSWORD / ... win over the file."""
        mapping = {
            "USER_ID": ("credentials", "user_id"),
            "PASSWORD": ("credentials", "password"),
            "TOTP_SECRET": ("credentials", "totp_secret"),
            "VENDOR_CODE": ("credentials", "vendor_code"),
            "API_SECRET": ("credentials", "api_secret"),
            "IMEI": ("credentials", "imei"),
            "LOGIN_METHOD": ("credentials", "login_method"),
            "ACCESS_TOKEN": ("credentials", "access_token"),
            "ACCOUNT_ID": ("credentials", "account_id"),
            "TELEGRAM_TOKEN": ("alerts", "telegram_token"),
            "TELEGRAM_CHAT_ID": ("alerts", "telegram_chat_id"),
        }
        for suffix, (section, attribute) in mapping.items():
            value = os.getenv(ENV_PREFIX + suffix)
            if value:
                setattr(getattr(self, section), attribute, value)

    # -- helpers ---------------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: Optional[os.PathLike] = None, redact: bool = True) -> Path:
        path = Path(path or DEFAULT_CONFIG_FILE)
        data = self.to_dict()
        if redact:
            for key in ("password", "totp_secret", "api_secret", "access_token"):
                if data["credentials"].get(key):
                    data["credentials"][key] = "***"
            if data["alerts"].get("telegram_token"):
                data["alerts"]["telegram_token"] = "***"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        return path


def configure_logging(level: int = 20, log_dir: str = "logs"):
    """One log file per run, same idea as the original Finvasia terminal."""
    import logging
    from datetime import datetime

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_format = "%(asctime)s %(levelname)s %(name)s :: %(message)s"

    handlers = [
        logging.FileHandler(Path(log_dir) / f"class_vii_{stamp}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ]
    logging.basicConfig(level=level, format=log_format, handlers=handlers)
    # Third-party libraries are extremely chatty at INFO.
    for noisy in ("websocket", "urllib3", "NorenRestApiPy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("class_vii")
