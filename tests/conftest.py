import shutil
from pathlib import Path

import pytest

from voiceops import settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def project_copy(tmp_path, monkeypatch):
    """A writable copy of the configs, prompts and scenarios, with settings pointed at it."""
    for folder in ("brands", "prompts", "scenarios"):
        shutil.copytree(REPO / folder, tmp_path / folder)
    shutil.copy(REPO / "voiceops.yaml", tmp_path / "voiceops.yaml")
    (tmp_path / "README.md").write_text(
        "# Test\n\n<!-- results:start -->\nnone yet\n<!-- results:end -->\n", encoding="utf-8")
    evals = tmp_path / "evals"
    for name, value in {
        "ROOT": tmp_path, "BRANDS_DIR": tmp_path / "brands", "PROMPTS_DIR": tmp_path / "prompts",
        "SCENARIOS_DIR": tmp_path / "scenarios", "EVALS_DIR": evals, "RUNS_DIR": evals / "runs",
        "SAMPLE_CALLS_DIR": evals / "sample_calls", "CACHE_PATH": evals / "cache.sqlite",
        "ACCEPTED_PATH": evals / "accepted.json", "PROJECT_FILE": tmp_path / "voiceops.yaml",
        "README": tmp_path / "README.md",
    }.items():
        monkeypatch.setattr(settings, name, value)
    return tmp_path


@pytest.fixture
def cfg():
    return settings.Settings(
        base_url="http://fake", api_key="test",
        agent=settings.RoleSettings("fake-agent", 0.3, 900),
        shopper=settings.RoleSettings("fake-shopper", 0.8, 600),
        judge=settings.RoleSettings("fake-judge", 0.0, 1500),
        reasoning_effort=None, json_mode=True, seed=7,
    )
