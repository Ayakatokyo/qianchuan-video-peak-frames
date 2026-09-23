import json
import argparse
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


class DynamicRpaClientTests(unittest.TestCase):
    def test_generated_client_self_test_exercises_dynamic_contract(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "rpa_dynamic_client.py"), "self-test"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("self-test passed", result.stdout)

    def test_default_fetch_output_is_under_qianchuan_resource_directory(self):
        import rpa_dynamic_client

        output = rpa_dynamic_client.default_output_dir()

        self.assertEqual(output.parents[1], ROOT / "千川数据资源")
        self.assertIn(
            "rpa.conn.juliang.qc.material.analysis.video.live.diagnosis",
            output.parts[-2],
        )

    def test_list_contract_rejects_unknown_business_field(self):
        import rpa_dynamic_client

        detail = {
            "inParamJsonSchemaText": None,
            "inParamList": [
                {"param": "material_id", "isRequired": True, "dataTypeEnum": "STRING"}
            ],
        }

        with self.assertRaises(rpa_dynamic_client.ValidationContractError):
            rpa_dynamic_client.validate_business_params(
                detail, {"material_id": "m-1", "typo": True}
            )

    def test_params_file_must_stay_inside_workspace(self):
        import rpa_dynamic_client

        args = argparse.Namespace(params_json=None, params_file="../outside.json")

        with self.assertRaisesRegex(
            rpa_dynamic_client.ValidationContractError, "workspace"
        ):
            rpa_dynamic_client.load_params(args)

    def test_runtime_config_uses_ycsessionid_for_cookie_and_session(self):
        import rpa_dynamic_client

        environment = {
            "ENV_BACKEND_HOST": "https://example.invalid/api",
            "YUCE_AUTHORIZATION": "auth-token",
            "YCSESSIONID": "browser-session",
        }
        with patch.dict(rpa_dynamic_client.os.environ, environment, clear=True):
            config = rpa_dynamic_client.runtime_config(require_session=True)

        self.assertEqual(config["session_id"], "browser-session")
        self.assertEqual(config["cookies"], {"YCSESSIONID": "browser-session"})


if __name__ == "__main__":
    unittest.main()
