import os
import sys
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True)
def isolate_user_directories(tmp_path, monkeypatch):
    """Prevent any test from reading or mutating real user config/ or work/ files."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_CONFIG_DIR", str(cfg_dir))

    try:
        from app import config_ui
    except ImportError:
        return

    monkeypatch.setattr(config_ui, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(config_ui, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(config_ui, "STATE_FILE", str(cfg_dir / ".config_state.json"))
    monkeypatch.setattr(config_ui, "SIGNAL_FILE", str(cfg_dir / ".config_complete"))
    monkeypatch.setattr(config_ui, "GEMINI_CONFIG", str(cfg_dir / "gemini_config.yaml"))
    monkeypatch.setattr(config_ui, "JOB_CONFIG", str(cfg_dir / "config.yaml"))
    monkeypatch.setattr(config_ui, "SECRETS_CONFIG", str(cfg_dir / "secrets.yaml"))
    monkeypatch.setattr(config_ui, "BIO_CONFIG", str(cfg_dir / "bio_config.yaml"))
    monkeypatch.setattr(config_ui, "BOT_STATUS_FILE", str(cfg_dir / ".bot_active"))
    monkeypatch.setattr(config_ui, "VERIFIED_STATUS", {})
