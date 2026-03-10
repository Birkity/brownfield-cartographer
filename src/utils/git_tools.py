"""
GitTools: per-file commit-velocity analysis using `git log`.

Wraps subprocess git calls with graceful fallback — every function returns
a safe default value when git is unavailable or the directory is not a repo.
"""

from __future__ import annotations

import logging
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30  # seconds


class GitVelocityResult:
    """
    Per-file commit counts over the analysed window.

    Attributes:
        commit_counts:  {posix_rel_path: num_commits}
        days:           Length of the analysis window.
        available:      False when git was unreachable — callers must check this.
    """

    def __init__(
        self,
        commit_counts: dict[str, int],
        days: int,
        repo_root: Path,
        available: bool = True,
    ) -> None:
        self.commit_counts = commit_counts
        self.days = days
        self.repo_root = repo_root
        self.available = available

    def for_file(self, rel_path: str | Path) -> int:
        """Return the commit count for *rel_path* (POSIX), or 0 if unknown."""
        key = Path(rel_path).as_posix()
        return self.commit_counts.get(key, 0)

    def top_files(self, n: int = 20) -> list[tuple[str, int]]:
        """Return the top-*n* highest-velocity files as (path, count) pairs."""
        return sorted(
            self.commit_counts.items(), key=lambda x: x[1], reverse=True
        )[:n]

    def pareto_core(self, threshold: float = 0.8) -> list[str]:
        """
        Return the minimum set of files that account for *threshold* of all commits.

        This is the "20% of files responsible for 80% of changes" set.
        """
        total = sum(self.commit_counts.values())
        if total == 0:
            return []
        target = total * threshold
        running = 0
        core: list[str] = []
        for path, count in self.top_files(n=len(self.commit_counts)):
            core.append(path)
            running += count
            if running >= target:
                break
        return core


def extract_git_velocity(repo_root: Path, days: int = 30) -> GitVelocityResult:
    """
    Count how many commits have touched each file in the last *days* days.

    Returns a GitVelocityResult with an empty dict when git is unavailable,
    not a repo, or returns a non-zero exit code.  Callers should check
    ``result.available`` before treating velocity data as meaningful.
    """
    since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        proc = subprocess.run(
            [
                "git",
                "log",
                f"--since={since_date}",
                "--name-only",
                "--pretty=format:",  # no commit metadata lines
                "--no-merges",
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=_GIT_TIMEOUT,
        )
    except FileNotFoundError:
        logger.warning("git not found — velocity data unavailable")
        return GitVelocityResult({}, days, repo_root, available=False)
    except subprocess.TimeoutExpired:
        logger.warning("git log timed out for %s", repo_root)
        return GitVelocityResult({}, days, repo_root, available=False)
    except Exception as exc:
        logger.warning("Unexpected git error: %s", exc)
        return GitVelocityResult({}, days, repo_root, available=False)

    if proc.returncode != 0:
        logger.warning(
            "git log exited %d for %s: %s",
            proc.returncode,
            repo_root,
            proc.stderr.strip(),
        )
        return GitVelocityResult({}, days, repo_root, available=False)

    counts: Counter[str] = Counter()
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip().replace("\\", "/")
        if line:
            counts[line] += 1

    logger.info(
        "GitVelocity: %d unique files touched in last %d days (%s)",
        len(counts),
        days,
        repo_root.name,
    )
    return GitVelocityResult(dict(counts), days, repo_root, available=True)


def get_last_commit_date(repo_root: Path, rel_path: Path) -> Optional[datetime]:
    """
    Return the datetime of the most recent commit that touched *rel_path*.

    Returns None gracefully when git is unavailable or the file has no history.
    """
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%aI", "--", rel_path.as_posix()],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return datetime.fromisoformat(proc.stdout.strip())
    except Exception:
        pass
    return None
