import unittest
from unittest.mock import patch

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dependency_bootstrap  # noqa: E402


class DependencyBootstrapTests(unittest.TestCase):
    def test_skips_install_when_all_prepare_dependencies_are_available(self):
        with patch.object(
            dependency_bootstrap.importlib.util,
            "find_spec",
            side_effect=lambda name: object() if name in {"requests", "imageio_ffmpeg"} else None,
        ), patch.object(dependency_bootstrap.subprocess, "run") as run:
            dependency_bootstrap.ensure_prepare_dependencies()

        run.assert_not_called()

    def test_installs_requirements_with_current_python_when_dependency_is_missing(self):
        completed = dependency_bootstrap.subprocess.CompletedProcess([], 0, "", "")
        with patch.object(
            dependency_bootstrap.importlib.util,
            "find_spec",
            side_effect=[object(), None, object(), object()],
        ), patch.object(dependency_bootstrap.shutil, "which", return_value="/usr/local/bin/uv"), patch.object(
            dependency_bootstrap.subprocess, "run", return_value=completed
        ) as run:
            dependency_bootstrap.ensure_prepare_dependencies()

        run.assert_called_once_with(
            [
                "/usr/local/bin/uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "-r",
                str(ROOT / "requirements.txt"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_reports_missing_modules_when_uv_is_unavailable(self):
        with patch.object(dependency_bootstrap.importlib.util, "find_spec", return_value=None), patch.object(
            dependency_bootstrap.shutil, "which", return_value=None
        ), self.assertRaises(dependency_bootstrap.DependencyBootstrapError) as error:
            dependency_bootstrap.ensure_prepare_dependencies()

        self.assertIn("requests, imageio-ffmpeg", str(error.exception))
        self.assertIn("uv", str(error.exception))

    def test_reports_uv_failure_without_hiding_its_output(self):
        completed = dependency_bootstrap.subprocess.CompletedProcess(
            [], 1, "", "error: unable to resolve packages"
        )
        with patch.object(dependency_bootstrap.importlib.util, "find_spec", return_value=None), patch.object(
            dependency_bootstrap.shutil, "which", return_value="uv"
        ), patch.object(dependency_bootstrap.subprocess, "run", return_value=completed), self.assertRaises(
            dependency_bootstrap.DependencyBootstrapError
        ) as error:
            dependency_bootstrap.ensure_prepare_dependencies()

        self.assertIn("error: unable to resolve packages", str(error.exception))


if __name__ == "__main__":
    unittest.main()
