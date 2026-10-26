"""Which code is running: the git commit it was built from (GET /api/version).

The Docker build runs this file on its own (`python build.py <.git dir> <out>`) to read the
commit from the checkout's .git, with no git binary, and writes build.json next to the app.
In development there's no build.json, so it asks git instead. Standard library only.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path


def build_file() -> Path:
    """Where the image keeps build.json: beside the app package (/app/backend/build.json).

    A function, not a constant: the Docker build runs this file from /build.py, where
    there's no parent directory to work it out from.
    """
    return Path(__file__).resolve().parent.parent / "build.json"


def commit_from_git_dir(git: Path) -> str | None:
    """The commit HEAD points at, read straight from a .git directory."""
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head or None  # a detached HEAD (as CI checks out) holds the commit itself
        ref = head.removeprefix("ref: ")
        loose = git / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        return None
    return None


def write_build_file(git: Path, out: Path) -> dict[str, str | None]:
    info = {"commit": commit_from_git_dir(git), "built_at": datetime.now(UTC).isoformat()}
    out.write_text(json.dumps(info), encoding="utf-8")
    return info


def _git_head() -> str | None:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 (development only)
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


@lru_cache
def build_info() -> dict[str, str | None]:
    """{"commit", "built_at"}: from build.json in the image, else from git (development)."""
    try:
        data = json.loads(build_file().read_text(encoding="utf-8"))
        return {"commit": data.get("commit"), "built_at": data.get("built_at")}
    except (OSError, ValueError):
        return {"commit": _git_head(), "built_at": None}


if __name__ == "__main__":
    print(json.dumps(write_build_file(Path(sys.argv[1]), Path(sys.argv[2]))))
