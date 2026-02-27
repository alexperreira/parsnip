import os
import subprocess
import sys
import unittest
from pathlib import Path


class CliLoadHelpTest(unittest.TestCase):
    def test_load_all_help(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        src_path = str(repo_root / "src")
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            src_path if not existing_pythonpath else src_path + os.pathsep + existing_pythonpath
        )

        result = subprocess.run(
            [sys.executable, "-m", "file_parser.cli", "load", "all", "--help"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("Load entities, events, conversations, and identity signals into SQLite.", output)
        self.assertIn("--overwrite", output)

    def test_load_chunks_help(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        src_path = str(repo_root / "src")
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            src_path if not existing_pythonpath else src_path + os.pathsep + existing_pythonpath
        )

        result = subprocess.run(
            [sys.executable, "-m", "file_parser.cli", "load", "chunks", "--help"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("Load chunks JSONL into SQLite.", output)
        self.assertIn("load chunks", output.lower())


if __name__ == "__main__":
    unittest.main()
