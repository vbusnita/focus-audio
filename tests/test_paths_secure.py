"""Owner-only path helpers, purge, and harden."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio import paths as paths_mod  # noqa: E402
from focus_audio.cli import main  # noqa: E402
from focus_audio.redact import PLACEHOLDER  # noqa: E402


def test_secure_mkdir_and_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    d = paths_mod.data_dir()
    assert d.is_dir()
    mode = stat.S_IMODE(d.stat().st_mode)
    assert mode & 0o077 == 0, f"data dir should be owner-only, got {oct(mode)}"

    f = d / "sample.txt"
    paths_mod.secure_write_text(f, "hello")
    fmode = stat.S_IMODE(f.stat().st_mode)
    assert fmode & 0o077 == 0
    assert f.read_text() == "hello"


def test_purge_cache_and_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    d = paths_mod.data_dir()
    cache = paths_mod.cache_dir()
    (cache / "a.txt").write_text("script")
    (cache / "a.mp3").write_bytes(b"\x00\x01")
    (d / "hook.log").write_text("log")
    (d / "config.toml").write_text("enabled = true\n")
    (d / "last_brief.md").write_text("brief")

    result = paths_mod.purge_runtime(cache=True, logs=True, last=True, root=d)
    assert result["removed_count"] >= 4
    assert not (cache / "a.txt").exists()
    assert not (d / "hook.log").exists()
    assert not (d / "last_brief.md").exists()
    # config must survive
    assert (d / "config.toml").is_file()


def test_purge_cli_requires_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    paths_mod.data_dir()
    rc = main(["purge"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "--yes" in out


def test_purge_cli_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    cache = paths_mod.cache_dir()
    (cache / "x.txt").write_text("gone")
    rc = main(["purge", "--yes", "--json"])
    assert rc == 0
    assert not (cache / "x.txt").exists()


def test_harden_runtime_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    d = paths_mod.data_dir()
    loose = d / "loose.txt"
    loose.write_text("x")
    os.chmod(loose, 0o644)
    os.chmod(d, 0o755)
    stats = paths_mod.harden_runtime_tree(d)
    assert stats["files"] >= 1
    assert stat.S_IMODE(d.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(loose.stat().st_mode) & 0o077 == 0


def test_pipeline_redacts_before_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """resolve_script should scrub keys even with skip_llm / no network."""
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    from focus_audio.config import Config
    from focus_audio.pipeline import resolve_script

    key = "xai-" + ("z" * 40)
    cfg = Config(mode="verbatim", skip_brief_words=9999, min_chars=1)
    ready = resolve_script(
        f"Deployed with key {key} successfully.",
        cfg,
        mode="verbatim",
        skip_llm=True,
    )
    assert key not in ready.script
    assert key not in ready.cleaned
    assert PLACEHOLDER in ready.script or PLACEHOLDER in ready.cleaned
