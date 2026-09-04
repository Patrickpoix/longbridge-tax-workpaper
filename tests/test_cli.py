import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import longbridge_tax_workpaper.cli as cli_module
from longbridge_tax_workpaper.cli import build_parser


def test_ocr_fallback_is_enabled_by_default_and_can_be_disabled():
    parser = build_parser()
    assert parser.parse_args(["statements"]).enable_ocr is True
    assert parser.parse_args(["statements", "--disable-ocr"]).enable_ocr is False


def test_parser_accepts_repeated_extra_input_dirs_and_moving_average_default():
    parser = build_parser()
    args = parser.parse_args([
        "statements",
        "--extra-input-dir", "prior-a",
        "--extra-input-dir", "prior-b",
    ])
    assert args.extra_input_dir == ["prior-a", "prior-b"]
    assert args.cost_basis_method == "MOVING_AVERAGE"


def test_interactive_parser_preserves_cost_method_and_extra_roots(monkeypatch):
    captured = {}

    def fake_run(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr(
        cli_module,
        "_interactive_prompt",
        lambda: (
            {
                "input_dir": "statements",
                "output_dir": "out",
                "tax_year": 2025,
                "password": None,
                "extra_input_dirs": ["prior-a", "prior-b"],
            },
            ["--cost-basis-method=FIFO", "--disable-ocr"],
        ),
    )
    monkeypatch.setattr(cli_module, "_run", fake_run)
    result = cli_module.main([])
    args = captured["args"]
    assert result == 0
    assert args.input_dir == "statements"
    assert args.extra_input_dir == ["prior-a", "prior-b"]
    assert args.cost_basis_method == "FIFO"
    assert args.enable_ocr is False


def test_module_help_runs_without_private_excel_runtime():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "scripts")
    result = subprocess.run(
        [sys.executable, "-m", "longbridge_tax_workpaper", "--help"],
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--password" not in result.stdout


def test_console_entrypoint_help_runs_after_install():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "scripts")

    # Prefer PATH lookup, but fall back to Scripts/ on Windows
    executable = shutil.which("longbridge-tax-workpaper")
    if executable is None and sys.platform == "win32":
        scripts = Path(sys.executable).parent / "Scripts"
        candidate = scripts / "longbridge-tax-workpaper.exe"
        if candidate.is_file():
            executable = str(candidate)

    if executable is None:
        pytest.skip("longbridge-tax-workpaper console entry point not found")

    result = subprocess.run(
        [executable, "--help"],
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--tax-year" in result.stdout
