import os
import subprocess
import sys
import unittest
from pathlib import Path


class CliThreadHelpTest(unittest.TestCase):
    def test_thread_help(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        src_path = str(repo_root / "src")
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            src_path if not existing_pythonpath else src_path + os.pathsep + existing_pythonpath
        )

        result = subprocess.run(
            [sys.executable, "-m", "file_parser.cli", "thread", "--help"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("Thread conversations across documents (Phase 10).", output)


if __name__ == "__main__":
    unittest.main()

