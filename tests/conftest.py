"""Keep tests away from the user's real data.

Before any test runs, every storage path (memory database, logs, activity log, call recordings,
voice prints, screenshots, code output, settings overrides) is pointed at a temporary folder, so
running the test suite never writes into data/ — which Nova's own "diagnose yourself" reads.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="nova-tests-"))


def pytest_configure(config):
    from app import config as config_mod

    config_mod.SETTINGS_FILE = _TMP / "settings.yaml"
    # Every (re)load of the configuration — tests that save settings reload it — must keep
    # pointing storage at the temp folder, or later objects would open the real data/ files.
    real_load = config_mod.load_config

    def isolated_load(*args, **kwargs):
        cfg = real_load(*args, **kwargs)
        cfg.storage.memory_db = str(_TMP / "memory.db")
        cfg.storage.logs_dir = str(_TMP / "logs")
        cfg.storage.screenshots_dir = str(_TMP / "screenshots")
        cfg.storage.code_output_dir = str(_TMP / "code_output")
        return cfg

    config_mod.load_config = isolated_load
    cfg = config_mod.get_config()
    config_mod.reload_config()  # forget the user's real settings.yaml (e.g. their assistant name)
    # folders addressed as "data/..." inside the app
    original_dir = cfg.__class__.dir

    def isolated_dir(self, relative: str):
        if str(relative).replace("\\", "/").startswith("data/"):
            relative = str(_TMP / str(relative)[5:])
        return original_dir(self, relative)

    cfg.__class__.dir = isolated_dir
    # the app logger writes to companion.log: send test logging to the temp folder instead
    from app import logger as logger_mod

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "baseFilename", "").endswith("companion.log"):
            root.removeHandler(handler)
            handler.close()
    logger_mod._configured = False
    logger_mod.setup_logging()


def pytest_unconfigure(config):
    logging.shutdown()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_real_writes_guard():
    """Fail loudly if a test ever touches the real memory database."""
    from app.config import ROOT

    real_db = ROOT / "data" / "memory.db"
    before = real_db.stat().st_mtime if real_db.exists() else None
    yield
    after = real_db.stat().st_mtime if real_db.exists() else None
    assert before == after, "a test wrote to the real data/memory.db"
