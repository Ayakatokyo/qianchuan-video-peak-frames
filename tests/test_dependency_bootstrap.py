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
            side_effect=lambda _name: object(),
        ), patch.object(dependency_bootstrap.subprocess, "run") as run:
            dependency_bootstrap.ensure_prepare_dependencies()

        run.assert_not_called()

    def test_installs_requirements_with_current_python_when_dependency_is_missing(self):
        installed = False

        def find_spec(name):
            if name == "imageio_ffmpeg" and not installed:
                return None
            return object()

        def install(*_args, **_kwargs):
            nonlocal installed
            installed = True
            return dependency_bootstrap.subprocess.CompletedProcess([], 0, "", "")

        with patch.object(
            dependency_bootstrap.importlib.util,
            "find_spec",
            side_effect=find_spec,
        ), patch.object(dependency_bootstrap.shutil, "which", return_value="/usr/local/bin/uv"), patch.object(
            dependency_bootstrap.subprocess, "run", side_effect=install
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

    def test_installs_requirements_when_jieba_is_missing(self):
        installed = False

        def find_spec(name):
            if name == "jieba" and not installed:
                return None
            return object()

        def install(*_args, **_kwargs):
            nonlocal installed
            installed = True
            return dependency_bootstrap.subprocess.CompletedProcess([], 0, "", "")

        with patch.object(
            dependency_bootstrap.importlib.util, "find_spec", side_effect=find_spec
        ), patch.object(
            dependency_bootstrap.shutil, "which", return_value="/usr/local/bin/uv"
        ), patch.object(
            dependency_bootstrap.subprocess, "run", side_effect=install
        ) as run:
            dependency_bootstrap.ensure_prepare_dependencies()

        run.assert_called_once()

    def test_reports_missing_modules_when_uv_is_unavailable(self):
        with patch.object(dependency_bootstrap.importlib.util, "find_spec", return_value=None), patch.object(
            dependency_bootstrap.shutil, "which", return_value=None
        ), self.assertRaises(dependency_bootstrap.DependencyBootstrapError) as error:
            dependency_bootstrap.ensure_prepare_dependencies()

        self.assertIn(
            "requests, imageio-ffmpeg, jsonschema, jieba",
            str(error.exception),
        )
        self.assertNotIn("openpyxl", str(error.exception))
        self.assertNotIn("Pillow", str(error.exception))
        self.assertIn("uv", str(error.exception))

    def test_workbook_dependencies_are_checked_only_when_requested(self):
        def find_spec(name):
            return None if name in {"openpyxl", "PIL"} else object()

        with patch.object(
            dependency_bootstrap.importlib.util, "find_spec", side_effect=find_spec
        ), patch.object(dependency_bootstrap.subprocess, "run") as run:
            dependency_bootstrap.ensure_prepare_dependencies()

        run.assert_not_called()

        with patch.object(
            dependency_bootstrap.importlib.util, "find_spec", side_effect=find_spec
        ), patch.object(
            dependency_bootstrap.shutil, "which", return_value=None
        ), self.assertRaises(dependency_bootstrap.DependencyBootstrapError) as error:
            dependency_bootstrap.ensure_prepare_dependencies(include_workbook=True)

        self.assertIn("openpyxl, Pillow", str(error.exception))

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
