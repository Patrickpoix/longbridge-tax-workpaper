import os
import shutil
import subprocess
import sys
from pathlib import Path

from longbridge_tax_workpaper.cli import build_parser


def test_ocr_fallback_is_enabled_by_default_and_can_be_disabled():
    parser = build_parser()
    assert parser.parse_args(["statements"]).enable_ocr is True
    assert parser.parse_args(["statements", "--disable-ocr"]).enable_ocr is False


def test_module_help_runs_without_private_excel_runtime():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-m", "longbridge_tax_workpaper", "--help"], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert ("artifact" + "_tool") not in result.stderr
    assert "--password" not in result.stdout


def test_console_entrypoint_help_runs_after_install():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    # Prefer PATH lookup, but fall back to Scripts/ on Windows
    executable = shutil.which("longbridge-tax-workpaper")
    if executable is None and sys.platform == "win32":
        scripts = Path(sys.executable).parent / "Scripts"
        candidate = scripts / "longbridge-tax-workpaper.exe"
        if candidate.is_file():
            executable = str(candidate)

    assert executable is not None, "longbridge-tax-workpaper not found on PATH or in Scripts/"
    result = subprocess.run([executable, "--help"], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "--tax-year" in result.stdout
