"""Filesystem locations for BrokerLedger data, logs, and client files."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _windows_appdata() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "BrokerLedger"
    return Path.home() / "AppData" / "Roaming" / "BrokerLedger"


def app_data_dir() -> Path:
    """Root directory for all app state. Override with BROKERLEDGER_HOME."""
    override = os.environ.get("BROKERLEDGER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform.startswith("win"):
        return _windows_appdata()
    return Path.home() / ".brokerledger"


def data_dir() -> Path:
    return app_data_dir() / "data"


def clients_dir() -> Path:
    return app_data_dir() / "clients"


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def backups_dir() -> Path:
    return app_data_dir() / "backups"


def db_path() -> Path:
    return data_dir() / "app.db"


def ensure_dirs() -> None:
    for d in (app_data_dir(), data_dir(), clients_dir(), logs_dir(), backups_dir()):
        d.mkdir(parents=True, exist_ok=True)
