"""Disposable virtual environments shared by interpreter-boundary tests."""
import os
import venv
from pathlib import Path


def create_venv(environment: Path) -> Path:
    """Build a pip-free environment and return its Python executable."""
    venv.EnvBuilder(with_pip=False).create(environment)
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
