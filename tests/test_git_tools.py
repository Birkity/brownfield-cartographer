import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from src.utils.git_tools import _git_command, extract_git_velocity, get_last_commit_date

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = WORKSPACE_ROOT / "tests" / ".tmp"


class GitToolsTests(unittest.TestCase):
    def _make_repo_root(self) -> Path:
        repo_root = TEST_TMP_ROOT / f"git_repo_{uuid.uuid4().hex}"
        repo_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(repo_root, ignore_errors=True))
        return repo_root

    def test_git_command_includes_repo_scoped_safe_directory(self) -> None:
        repo_root = self._make_repo_root()
        command = _git_command(repo_root, "log", "--name-only")

        self.assertEqual(command[:2], ["git", "-c"])
        self.assertIn("safe.directory=", command[2])
        self.assertEqual(command[3:], ["log", "--name-only"])

    def test_extract_git_velocity_and_last_commit_date_work_on_local_repo(self) -> None:
        repo_root = self._make_repo_root()
        target_file = repo_root / "app.py"
        target_file.write_text("print('hello')\n", encoding="utf-8")

        subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.name", "Cartographer Tests"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "cartographer-tests@example.com"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["git", "add", "app.py"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )

        velocity = extract_git_velocity(repo_root, days=30)
        self.assertTrue(velocity.available)
        self.assertEqual(velocity.for_file("app.py"), 1)

        last_modified = get_last_commit_date(repo_root, Path("app.py"))
        self.assertIsNotNone(last_modified)


if __name__ == "__main__":
    unittest.main()
