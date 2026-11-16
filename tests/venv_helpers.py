"""Disposable virtual environments shared by interpreter-boundary tests."""
import os
import venv
from pathlib import Path


def create_venv(environment: Path) -> Path:
    """Use symlinks on POSIX so uv's macOS Python can still locate libpython."""
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment)
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
