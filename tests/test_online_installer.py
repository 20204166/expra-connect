from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install" / "install-online.sh"


class OnlineInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.fixtures = self.root / "downloads"
        self.fixtures.mkdir()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.venv_dir = self.root / "venv"
        self._write_fake_curl()
        self._write_fake_python()
        self._write_manifest()

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _write_fake_curl(self) -> None:
        self._write_executable(
            self.bin_dir / "curl",
            """#!/usr/bin/env bash
set -euo pipefail
output=
url=
while (($#)); do
    if [[ $1 == --output ]]; then output=$2; shift 2; else url=$1; shift; fi
done
[[ -n $output && -n $url ]]
cp "$FIXTURE_DIR/${url##*/}" "$output"
""",
        )

    def _write_fake_python(self) -> None:
        self._write_executable(
            self.bin_dir / "python3",
            """#!/usr/bin/env bash
set -euo pipefail
if [[ $1 == -c ]]; then exit 0; fi
if [[ $1 == -m && $2 == venv ]]; then
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<'PYTHON'
#!/usr/bin/env bash
set -euo pipefail
if [[ $1 == -m && $2 == pip ]]; then
    wheel=${@: -1}
    version=${wheel##*/}
    version=${version#expra_connect-}
    version=${version%-py3-none-any.whl}
    printf '%s\\n' "$version" > "$VENV_VERSION_FILE"
    exit 0
fi
if [[ $1 == -c ]]; then cat "$VENV_VERSION_FILE"; exit 0; fi
exit 2
PYTHON
    chmod +x "$3/bin/python"
    touch "$VENV_CREATED_MARKER"
    exit 0
fi
exit 2
""",
        )

    def _write_manifest(self, *, bad_latest_hash: bool = False) -> None:
        previous = "expra_connect-2.2.3.2-py3-none-any.whl"
        latest = "expra_connect-2.2.3.3-py3-none-any.whl"
        native_only = "expra_connect-2.2.3.4-cp312-cp312-linux_x86_64.whl"
        wheel_data = {
            previous: b"previous universal wheel",
            latest: b"latest universal wheel",
            native_only: b"newer native wheel",
        }
        rows = []
        for filename, data in wheel_data.items():
            (self.fixtures / filename).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            if bad_latest_hash and filename == latest:
                digest = "0" * 64
            rows.append(f"{digest}  {filename}")
        (self.fixtures / "SHA256SUMS").write_text(
            "\n".join(rows) + "\n", encoding="utf-8"
        )

    def _run_installer(self) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env['PATH']}",
                "HOME": str(self.home),
                "EXPRA_CONNECT_VENV": str(self.venv_dir),
                "FIXTURE_DIR": str(self.fixtures),
                "VENV_CREATED_MARKER": str(self.root / "venv-created"),
                "VENV_VERSION_FILE": str(self.root / "venv-version"),
            }
        )
        return subprocess.run(
            ["bash", str(INSTALLER)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_installs_latest_checksum_verified_universal_wheel(self) -> None:
        result = self._run_installer()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2.2.3.3", result.stdout)
        self.assertTrue((self.venv_dir / "bin" / "python").is_file())

    def test_rejects_wheel_when_manifest_checksum_does_not_match(self) -> None:
        self._write_manifest(bad_latest_hash=True)

        result = self._run_installer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stderr.lower())
        self.assertFalse((self.root / "venv-created").exists())


if __name__ == "__main__":
    unittest.main()
