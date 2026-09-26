import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import msgpack


class ConnectMcpIdentityInspectionTests(unittest.TestCase):
    def test_static_probe_reports_messagepack_key_presence_without_values(self) -> None:
        repository = Path(__file__).parents[1]
        adapter = (
            repository
            / "tools"
            / "expra_connect_mcp"
            / "src"
            / "expra_connect_dev_mcp"
            / "connect_adapter.py"
        )
        module = ast.parse(adapter.read_text(encoding="utf-8"))
        probe = next(
            ast.literal_eval(statement.value)
            for statement in module.body
            if isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_STATIC_PROBE"
                for target in statement.targets
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()

            def write_messagepack(path: Path, document: dict[str, object]) -> None:
                packed = msgpack.packb(document, use_bin_type=True)
                assert isinstance(packed, bytes)
                path.write_bytes(packed)

            (profile / "identity.json").write_text(
                json.dumps({"schema_version": 3, "node_id": "peer-a"}),
                encoding="utf-8",
            )
            write_messagepack(
                profile / "identity.msgpack",
                {
                    "schema_version": 1,
                    "node_id": "peer-a",
                    "secret": b"secret-that-must-not-be-reported",
                    "root_private_key": b"private-key-that-must-not-be-reported",
                },
            )
            (profile / "device_identity.json").write_text(
                json.dumps({"schema_version": 2, "node_id": "peer-a"}),
                encoding="utf-8",
            )
            write_messagepack(
                profile / "device_identity.msgpack",
                {
                    "schema_version": 1,
                    "node_id": "peer-a",
                    "private_key": b"device-key-that-must-not-be-reported",
                },
            )

            result = subprocess.run(
                [sys.executable, "-c", probe, str(root), "identity"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )

        report = json.loads(result.stdout)
        files = {item["path"]: item for item in report["state_files"]}
        identity_secrets = files.get("profile/identity.msgpack")
        device_secrets = files.get("profile/device_identity.msgpack")
        self.assertIsNotNone(identity_secrets)
        self.assertIsNotNone(device_secrets)
        assert identity_secrets is not None
        assert device_secrets is not None
        self.assertEqual(
            identity_secrets["secret_fields_present"],
            ["root_private_key", "secret"],
        )
        self.assertEqual(
            device_secrets["secret_fields_present"],
            ["private_key"],
        )
        self.assertNotIn("secret-that-must-not-be-reported", result.stdout)
        self.assertNotIn("private-key-that-must-not-be-reported", result.stdout)
        self.assertNotIn("device-key-that-must-not-be-reported", result.stdout)


if __name__ == "__main__":
    unittest.main()
