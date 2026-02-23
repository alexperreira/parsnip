import os
import subprocess
import sys
import unittest
from pathlib import Path


class CliRunHelpTest(unittest.TestCase):
    def test_run_help_includes_llm_provider_options(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        src_path = str(repo_root / "src")
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            src_path if not existing_pythonpath else src_path + os.pathsep + existing_pythonpath
        )

        result = subprocess.run(
            [sys.executable, "-m", "file_parser.cli", "run", "--help"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("--llm-provider", output)
        self.assertIn("--llm-model", output)
        self.assertIn("--llm-openai-base-url", output)
        self.assertIn("--llm-gemini-base-url", output)
        self.assertIn("extract-text,chunk,triage,l", output)
        self.assertIn("--triage-max-llm-chunks", output)


if __name__ == "__main__":
    unittest.main()
