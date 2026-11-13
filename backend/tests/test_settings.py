"""Smoke test: settings load defaults and read env overrides."""
from datetime import date
from pathlib import Path

from app.settings import PROJECT_ROOT, Settings


def test_defaults_point_at_repo_data_dirs():
    """Default data and sandbox paths resolve under the repo root, independent of cwd."""
    s = Settings(_env_file=None)
    assert s.data_dir == PROJECT_ROOT / "data" / "technical-case" / "data"
    assert s.sandbox_dir == PROJECT_ROOT / "data" / "sandbox"
    assert s.default_term_months == 12
    assert s.as_of_date is None


def test_env_overrides_are_read(monkeypatch, tmp_path: Path):
    """Env vars override defaults, including the as-of date and term length."""
    monkeypatch.setenv("AS_OF_DATE", "2025-12-31")
    monkeypatch.setenv("DEFAULT_TERM_MONTHS", "24")
    monkeypatch.setenv("SANDBOX_DIR", str(tmp_path))
    s = Settings(_env_file=None)
    assert s.as_of_date == date(2025, 12, 31)
    assert s.default_term_months == 24
    assert s.sandbox_dir == tmp_path
