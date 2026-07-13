from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "runtime-fingerprint.py"


class RuntimeFingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tempdir.name) / "venv"
        runtime_bin = self.runtime / "bin"
        runtime_bin.mkdir(parents=True)
        (runtime_bin / "python").write_text("managed interpreter fixture\n", encoding="utf-8")
        (runtime_bin / "litellm").write_text("#!/managed/python\n", encoding="utf-8")
        (self.runtime / "pyvenv.cfg").write_text(
            "home = /managed/python\nversion = 3.13.14\n", encoding="utf-8"
        )
        self.site_packages = self.runtime / "lib" / "python3.13" / "site-packages"
        package = self.site_packages / "example"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.site_packages / "example-1.0.dist-info").mkdir()
        (self.site_packages / "example-1.0.dist-info" / "METADATA").write_text(
            "Name: example\nVersion: 1.0\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_helper(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(SCRIPT),
                "--runtime-root",
                str(self.runtime),
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def baseline(self) -> str:
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{64}$")
        return result.stdout.strip()

    def test_tampered_package_byte_fails_expected_fingerprint(self) -> None:
        expected = self.baseline()
        package_file = self.site_packages / "example" / "__init__.py"
        package_file.write_text("VALUE = 2\n", encoding="utf-8")

        result = self.run_helper("--expect", expected)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime fingerprint mismatch", result.stderr)

    def test_added_unlisted_site_packages_file_fails_expected_fingerprint(self) -> None:
        expected = self.baseline()
        (self.site_packages / "injected.pth").write_text(
            "import injected_runtime_code\n", encoding="utf-8"
        )

        result = self.run_helper("--expect", expected)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime fingerprint mismatch", result.stderr)

    def test_added_pycache_fails_expected_fingerprint(self) -> None:
        expected = self.baseline()
        cache = self.site_packages / "example" / "__pycache__"
        cache.mkdir()
        (cache / "__init__.cpython-313.pyc").write_bytes(b"runtime cache")

        result = self.run_helper("--expect", expected)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime fingerprint mismatch", result.stderr)

    def test_tampered_runtime_launcher_fails_expected_fingerprint(self) -> None:
        expected = self.baseline()
        (self.runtime / "bin" / "litellm").write_text(
            "#!/managed/python\nimport injected_runtime_code\n", encoding="utf-8"
        )

        result = self.run_helper("--expect", expected)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime fingerprint mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
