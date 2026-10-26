"""Is the live app on the latest main? Run before fixing anything reported from it.

    python scripts/check-deployed.py [URL]

Asks the app's /api/version which commit it runs, fetches origin/main, and compares.
Exit 0: up to date. Exit 1: behind (update and retest first) or can't tell. Standard
library only, so it runs with any Python 3.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://bawbee.example.com"
UPDATE = "cd /opt/bawbee && git pull && docker compose up -d --build"


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 (fixed git commands, run by hand)
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BAWBEE_URL", DEFAULT_URL)
    url = url.rstrip("/")
    if not url.startswith(("https://", "http://")):
        print(f"UNKNOWN: {url} isn't an http(s) address.")
        return 1
    git("fetch", "--quiet", "origin", "main")
    latest = git("rev-parse", "origin/main")
    latest_line = git("log", "-1", "--format=%h %s (%cs)", latest)
    try:
        # A named user agent: the proxy in front of the app refuses Python's default one.
        request = urllib.request.Request(  # noqa: S310 (http(s) only, checked above)
            f"{url}/api/version", headers={"User-Agent": "bawbee-check-deployed"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            live = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"BEHIND: {url} has no /api/version, so it predates it.")
            print(f"  latest main: {latest_line}")
            print(f"  update: {UPDATE}")
            return 1
        print(f"UNKNOWN: {url}/api/version answered {exc.code}.")
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"UNKNOWN: couldn't reach {url}: {exc}")
        return 1

    commit = live.get("commit")
    built = live.get("built_at") or "unknown time"
    schema = live.get("schema_revision")
    if not commit:
        print(
            f"UNKNOWN: {url} doesn't know its commit (built {built}). Rebuild from a git checkout."
        )
        return 1
    if commit == latest:
        print(f"UP TO DATE: {url} runs {latest_line}, built {built}, schema {schema}.")
        return 0
    try:
        behind = git("log", "--format=  - %h %s", f"{commit}..{latest}")
        live_line = git("log", "-1", "--format=%h %s (%cs)", commit)
    except subprocess.CalledProcessError:
        print(f"UNKNOWN: {url} runs {commit[:7]}, which isn't on origin/main.")
        return 1
    if not behind:
        print(f"AHEAD: {url} runs {live_line}, newer than origin/main ({latest_line}).")
        return 0
    count = len(behind.splitlines())
    print(f"BEHIND by {count} commit{'s' if count != 1 else ''}: {url} runs {live_line}.")
    print(f"  latest main: {latest_line}")
    print("  missing:")
    print(behind)
    print(f"  update: {UPDATE}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
