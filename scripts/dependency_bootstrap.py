"""Install the optional runtime packages required by the prepare workflow."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys


PREPARE_DEPENDENCIES = {
    "requests": "requests",
    "imageio_ffmpeg": "imageio-ffmpeg",
}
REQUIREMENTS_PATH = Path(__file__).resolve().parents[1] / "requirements.txt"


class DependencyBootstrapError(RuntimeError):
    """Raised when prepare dependencies cannot be made available."""


def _missing_prepare_dependencies():
    return [
        distribution
        for module, distribution in PREPARE_DEPENDENCIES.items()
        if importlib.util.find_spec(module) is None
    ]


def ensure_prepare_dependencies(*, allow_install=True):
    """Install missing prepare dependencies into this process's Python environment."""
    missing = _missing_prepare_dependencies()
    if not missing:
        return
    if not allow_install:
        raise DependencyBootstrapError("local workflow requires installed dependencies: " + ", ".join(missing))

    uv = shutil.which("uv")
    if not uv:
        raise DependencyBootstrapError(
            "prepare requires missing dependencies: "
            + ", ".join(missing)
            + ". Install uv, then retry prepare."
        )

    try:
        result = subprocess.run(
            [uv, "pip", "install", "--python", sys.executable, "-r", str(REQUIREMENTS_PATH)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise DependencyBootstrapError(
            f"unable to start uv while installing {', '.join(missing)}: {exc}"
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "uv exited without output").strip()
        raise DependencyBootstrapError(
            f"unable to install prepare dependencies ({', '.join(missing)}): {detail}"
        )

    still_missing = _missing_prepare_dependencies()
    if still_missing:
        raise DependencyBootstrapError(
            "uv completed but prepare dependencies are unavailable to "
            f"{sys.executable}: {', '.join(still_missing)}"
        )
