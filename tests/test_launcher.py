"""Exercise shell bootstrap paths without launching a desktop or installing packages."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="olivia launcher ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy2(ROOT / "run.sh", self.root / "run.sh")
        portrait = self.root / "assets/portraits/idle.png"
        portrait.parent.mkdir(parents=True)
        portrait.touch()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.executable(self.bin / "uname", '#!/bin/bash\necho Darwin\n')
        self.env = os.environ.copy()
        for key in ("OLIVIA_PYTHON", "QT_QPA_PLATFORM"):
            self.env.pop(key, None)
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env["PATH"], OLIVIA_SKIP_RENDER="1")

    def executable(self, path, script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(script)
        path.chmod(0o755)

    def interpreter(self, path, imports=True):
        self.executable(path, '#!/bin/bash\nif [[ "$1" == -c ]]; then exit ' + ('0' if imports else '1') + '; fi\nprintf "%s\\n" "$PWD" "$*" "$QT_QPA_PLATFORM"\n')

    def run_launcher(self):
        return subprocess.run(["bash", str(self.root / "run.sh"), "--example"],
                              cwd="/", env=self.env, text=True, capture_output=True, timeout=10)

    def test_project_venv_and_cocoa_from_another_directory_with_spaces(self):
        self.interpreter(self.root / ".venv/bin/python")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(Path(lines[0]).resolve(), self.root.resolve())
        self.assertEqual(lines[1:], ["pet.py --example", "cocoa"])

    def test_explicit_interpreter_and_test_platform_are_preserved(self):
        custom = self.root / "custom python"
        self.interpreter(custom)
        self.interpreter(self.root / ".venv/bin/python", imports=False)
        self.env.update(OLIVIA_PYTHON=str(custom), QT_QPA_PLATFORM="offscreen")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "offscreen")

    def test_missing_qt_has_setup_instructions(self):
        self.interpreter(self.root / ".venv/bin/python", imports=False)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn("setup_macos.sh", result.stderr)

    def test_missing_required_portrait_fails_before_launch(self):
        self.interpreter(self.root / ".venv/bin/python")
        (self.root / "assets/portraits/idle.png").unlink()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn("assets/portraits/idle.png", result.stderr)
