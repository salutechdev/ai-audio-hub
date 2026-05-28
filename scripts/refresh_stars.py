#!/usr/bin/env python3
"""Refresh docs/stars.json with current GitHub star counts for every item that has a GitHub_Link.

Run locally (uses `gh` if available, falls back to anonymous GitHub API):
    python3 scripts/refresh_stars.py

In CI: invoked by .github/workflows/refresh-stars.yml on a weekly cron and on demand.
"""

from __future__ import annotations
import json
import os
import re
import sys
import subprocess
import time
from datetime import datetime, timezone

ITEMS_DIR = os.path.join(os.path.dirname(__file__), "..", "items")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "stars.json")


def parse_owner_repo(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL. Tolerates trailing slashes, /tree/* paths, and .git suffixes."""
    if not url or "github.com" not in url:
        return None
    m = re.search(r"github\.com/([^/]+)/([^/#?]+)", url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    repo = re.sub(r"\.git$", "", repo)
    # Skip obviously-wrong slugs like "github.com" / "facebookresearch" alone or self-org references that aren't repos.
    if owner.lower() in {"github.com", "www.github.com"}:
        return None
    return owner, repo


def fetch_stars(owner: str, repo: str) -> int | None:
    """Try `gh api` first (authenticated, higher rate limit); fall back to anonymous curl."""
    # gh path
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}", "--jq", ".stargazers_count"],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(out.stdout.strip())
    except FileNotFoundError:
        pass
    # anonymous fallback
    try:
        out = subprocess.run(
            ["curl", "-sf", f"https://api.github.com/repos/{owner}/{repo}"],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            return data.get("stargazers_count")
    except Exception:
        return None
    return None


def main() -> int:
    pairs: dict[str, tuple[str, str]] = {}  # abbrev -> (owner, repo)
    for f in sorted(os.listdir(ITEMS_DIR)):
        if not f.endswith(".json"):
            continue
        d = json.load(open(os.path.join(ITEMS_DIR, f), encoding="utf-8"))
        slug = parse_owner_repo(d.get("GitHub_Link", ""))
        if slug:
            pairs[d["Abbreviation"]] = slug

    print(f"Refreshing stars for {len(pairs)} entries…", file=sys.stderr)
    existing = {}
    if os.path.exists(OUT_PATH):
        try:
            existing = json.load(open(OUT_PATH, encoding="utf-8"))
        except Exception:
            existing = {}

    out: dict[str, dict] = {}
    for i, (abbrev, (owner, repo)) in enumerate(pairs.items(), 1):
        stars = fetch_stars(owner, repo)
        if stars is None:
            # keep stale value rather than dropping the entry
            prev = existing.get(abbrev)
            if prev:
                out[abbrev] = prev
                print(f"  [{i}/{len(pairs)}] {abbrev}: keep stale ({prev.get('stars')}★)", file=sys.stderr)
            else:
                print(f"  [{i}/{len(pairs)}] {abbrev}: FAILED (no prior value)", file=sys.stderr)
            continue
        out[abbrev] = {
            "owner": owner,
            "repo": repo,
            "stars": stars,
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        print(f"  [{i}/{len(pairs)}] {abbrev}: {stars}★  ({owner}/{repo})", file=sys.stderr)
        time.sleep(0.1)  # gentle to the API

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as out_file:
        json.dump(out, out_file, indent=2, sort_keys=True, ensure_ascii=False)
        out_file.write("\n")
    print(f"Wrote {OUT_PATH} ({len(out)} entries).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
