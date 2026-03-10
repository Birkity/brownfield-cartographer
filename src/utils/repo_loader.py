"""
RepoLoader: resolves a target (local path or GitHub URL) to a local filesystem path.

If a GitHub URL is supplied the repo is cloned with `git clone --depth 50`.
Clones are cached in `<clone_base>/` to avoid re-downloading on subsequent runs.

Security note: only https://github.com/<owner>/<repo> URLs are accepted.
All other URLs are rejected to prevent SSRF.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Allow only GitHub HTTPS URLs — rejects IP literals, other hosts, auth tokens
_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(\.git)?/?$"
)


class RepoLoadError(Exception):
    """Raised when a repository cannot be resolved to a local path."""


def resolve_repo(target: str, clone_base: Path | None = None) -> Path:
    """
    Resolve *target* to an absolute local Path.

    Args:
        target:      A local directory path or a GitHub HTTPS URL.
        clone_base:  Where to put cloned repos.  Defaults to a system temp dir.

    Returns:
        Absolute Path pointing at the repo root.

    Raises:
        RepoLoadError: path doesn't exist, isn't a directory, or clone fails.
    """
    if _is_url(target):
        return _clone_github(target, clone_base)

    path = Path(target).expanduser().resolve()
    if not path.exists():
        raise RepoLoadError(f"Path does not exist: {path}")
    if not path.is_dir():
        raise RepoLoadError(f"Path is not a directory: {path}")

    logger.info("Using local repo: %s", path)
    return path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_url(target: str) -> bool:
    try:
        parsed = urlparse(target)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _clone_github(url: str, clone_base: Path | None) -> Path:
    """Clone a validated GitHub URL and return the local path."""
    if not _GITHUB_URL_RE.match(url):
        raise RepoLoadError(
            f"Only https://github.com/<owner>/<repo> URLs are supported. Got: {url!r}"
        )

    # Derive a safe directory name from the URL
    repo_slug = url.rstrip("/").rstrip(".git").rsplit("/", 1)[-1]

    if clone_base is None:
        # Keep clones across runs when running interactively — use a stable temp location
        clone_base = Path(tempfile.gettempdir()) / "cartographer_clones"

    clone_base.mkdir(parents=True, exist_ok=True)
    dest = clone_base / repo_slug

    if dest.exists() and (dest / ".git").exists():
        logger.info("Reusing cached clone at %s", dest)
        return dest

    logger.info("Cloning %s → %s", url, dest)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth=50", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        raise RepoLoadError(
            "git executable not found. Install Git to use GitHub URL targets."
        )
    except subprocess.TimeoutExpired:
        raise RepoLoadError(f"git clone timed out for {url}")

    if result.returncode != 0:
        raise RepoLoadError(
            f"git clone failed for {url}:\n{result.stderr.strip()}"
        )

    logger.info("Clone complete: %s", dest)
    return dest
