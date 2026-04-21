"""Test fixtures: isolated data dir + fresh DB per test."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from brokerledger import config, db, paths  # noqa: F401
from brokerledger.auth.service import create_user, login
from brokerledger.db import engine as db_engine
from brokerledger.db.seed import run_all_seeds


@pytest.fixture(autouse=True)
def _isolated_app_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BROKERLEDGER_HOME", str(tmp_path))
    monkeypatch.setenv("BROKERLEDGER_FAKE_LLM", "1")
    config.reset_settings_for_tests()
    db_engine.reset_for_tests()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    yield
    db_engine.reset_for_tests()
    config.reset_settings_for_tests()


@pytest.fixture
def db_session():
    db_engine.init_engine()
    run_all_seeds()
    return db_engine.session_scope


@pytest.fixture
def logged_in_admin(db_session):
    create_user("testadmin", "TestPassword1", role="admin", full_name="Test Admin")
    return login("testadmin", "TestPassword1")
