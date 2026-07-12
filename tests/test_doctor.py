"""Tests for focus-audio doctor (no real secrets required)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio import cli  # noqa: E402
from focus_audio.doctor import (  # noqa: E402
    Check,
    DoctorReport,
    format_doctor_text,
    run_doctor,
)


def test_run_doctor_plugin_layout_ok():
    report = run_doctor(plugin_root=ROOT)
    layout = next(c for c in report.checks if c.id == "plugin_layout")
    assert layout.ok is True
    assert layout.level == "ok"


def test_run_doctor_missing_layout_fails(tmp_path: Path):
    report = run_doctor(plugin_root=tmp_path)
    layout = next(c for c in report.checks if c.id == "plugin_layout")
    assert layout.ok is False
    assert layout.level == "fail"
    assert report.ok is False


def test_format_doctor_text_includes_overall():
    report = DoctorReport(
        ok=True,
        version="0.4.0",
        checks=[
            Check(id="python", ok=True, level="ok", detail="Python 3.12"),
            Check(
                id="api_key",
                ok=False,
                level="fail",
                detail="missing",
                fix="export XAI_API_KEY=…",
            ),
        ],
    )
    text = format_doctor_text(report)
    assert "Focus Audio doctor" in text
    assert "api_key" in text
    assert "export XAI_API_KEY" in text


def test_cmd_doctor_json(capsys):
    with patch("focus_audio.doctor.run_doctor") as rd:
        rd.return_value = DoctorReport(
            ok=True,
            version="0.4.0",
            checks=[Check(id="python", ok=True, level="ok", detail="ok")],
        )
        rc = cli.cmd_doctor(type("A", (), {"json": True})())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["version"] == "0.4.0"
    assert out["checks"][0]["id"] == "python"


def test_cmd_doctor_fails_exit_code(monkeypatch, capsys):
    with patch("focus_audio.doctor.run_doctor") as rd:
        rd.return_value = DoctorReport(
            ok=False,
            version="0.4.0",
            checks=[
                Check(
                    id="api_key",
                    ok=False,
                    level="fail",
                    detail="missing",
                    fix="set key",
                )
            ],
        )
        rc = cli.cmd_doctor(type("A", (), {"json": False})())
    assert rc == 1
    assert "NEEDS ATTENTION" in capsys.readouterr().out
