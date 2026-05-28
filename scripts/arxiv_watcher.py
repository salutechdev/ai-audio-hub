#!/usr/bin/env python3
"""Discover new audio-AI papers on arXiv and open triage issues.

Behavior:
  1. Query arXiv for recent papers (last LOOKBACK_DAYS) in cs.SD / eess.AS / cs.CL
     whose abstract OR title contains one of the AUDIO_AI keywords.
  2. Skip a paper if its arxiv id is already referenced from any items/*.json (Paper_Link).
  3. Skip a paper if a watcher-opened GitHub issue already mentions that arxiv id.
  4. For the remaining candidates, open an issue with title prefix [Auto-suggested]
     and a body that loosely mirrors the add-paper.yml fields so the maintainer
     can copy the JSON quickly.

Tuning knobs are constants at the top. The script honours `GH_REPO` env var (set
by the workflow to `BinWang28/audio-ai-hub`) so it can be tested against a fork
without code changes. Dry-run: `DRY_RUN=1 python3 scripts/arxiv_watcher.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# --- Tuning ---------------------------------------------------------------

LOOKBACK_DAYS = 14
MAX_RESULTS_PER_CATEGORY = 200
MAX_ISSUES_PER_RUN = 8   # don't spam — cap how many issues a single run opens

CATEGORIES = ["cs.SD", "eess.AS", "cs.CL"]

# A paper qualifies if its title or abstract contains any of these terms (case-insensitive).
# Phrased to cover audio LLM / ASR / TTS / music gen / audio gen / codec / benchmark.
AUDIO_AI_KEYWORDS = [
    r"audio[- ]language model", r"speech[- ]language model", r"audio[- ]LLM",
    r"speech LLM", r"large audio language", r"omni[- ]modal",
    r"speech recognition", r"speech translation", r"speech[- ]to[- ]speech",
    r"text[- ]to[- ]speech\b", r"\bTTS\b", r"voice cloning", r"zero[- ]shot TTS",
    r"music generation", r"audio generation", r"sound generation",
    r"neural audio codec", r"audio codec",
    r"spoken dialogue", r"speech dialogue", r"speech chatbot",
    r"audio understanding", r"audio reasoning", r"audio question answer",
    r"audio benchmark", r"speech benchmark",
]
KEYWORD_RE = re.compile("|".join(AUDIO_AI_KEYWORDS), re.IGNORECASE)

ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# --- Helpers --------------------------------------------------------------

def existing_paper_ids() -> set[str]:
    """All arxiv ids referenced by items/*.json (Paper_Link), as bare e.g. '2503.20215'."""
    out: set[str] = set()
    items_dir = os.path.join(os.path.dirname(__file__), "..", "items")
    for f in os.listdir(items_dir):
        if not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(items_dir, f), encoding="utf-8"))
        except Exception:
            continue
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", d.get("Paper_Link", ""))
        if m:
            out.add(m.group(1))
    return out


def already_suggested_ids(repo: str) -> set[str]:
    """Look for prior watcher-opened issues (by label) and harvest arxiv ids from their bodies."""
    out: set[str] = set()
    try:
        r = subprocess.run(
            ["gh", "issue", "list", "-R", repo, "--label", "auto-suggested",
             "--state", "all", "--limit", "300", "--json", "title,body"],
            capture_output=True, text=True, check=True, timeout=60,
        )
        for it in json.loads(r.stdout):
            for m in re.finditer(r"\b([0-9]{4}\.[0-9]{4,5})\b", (it.get("title", "") + " " + it.get("body", ""))):
                out.add(m.group(1))
    except Exception as e:
        print(f"warn: couldn't enumerate prior auto-suggested issues: {e}", file=sys.stderr)
    return out


USER_AGENT = (
    "audio-ai-hub-watcher/1.0 "
    "(+https://github.com/BinWang28/audio-ai-hub; contact: bwang28c@gmail.com)"
)


def _arxiv_get(url: str, max_retries: int = 4) -> str:
    """GET arxiv URL with exponential backoff on 429 / timeout / 5xx.

    arXiv asks API consumers to set a descriptive User-Agent including a
    contact address; doing so substantially reduces 429 risk on shared
    cloud runners.
    """
    delay = 5
    last_exc = None
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code == 429 or 500 <= e.code < 600:
                print(f"  arxiv {e.code} on attempt {attempt}/{max_retries}; backing off {delay}s", file=sys.stderr)
                time.sleep(delay)
                delay = min(delay * 2, 90)
                continue
            raise
        except Exception as e:
            last_exc = e
            print(f"  arxiv error '{e}' on attempt {attempt}/{max_retries}; backing off {delay}s", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 90)
    raise last_exc if last_exc else RuntimeError("arxiv exhausted retries")


def arxiv_query(category: str, since: datetime) -> list[dict]:
    """Fetch recent papers in `category` submitted on/after `since` (UTC)."""
    q = f"cat:{category}+AND+submittedDate:[{since.strftime('%Y%m%d')}0000+TO+999912312359]"
    url = (
        "http://export.arxiv.org/api/query?"
        f"search_query={q}&start=0&max_results={MAX_RESULTS_PER_CATEGORY}"
        "&sortBy=submittedDate&sortOrder=descending"
    )
    body = _arxiv_get(url)
    root = ET.fromstring(body)
    rows = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        idu = entry.findtext("atom:id", default="", namespaces=ARXIV_NS)
        m = re.search(r"abs/([0-9]{4}\.[0-9]{4,5})", idu)
        if not m:
            continue
        title = (entry.findtext("atom:title", default="", namespaces=ARXIV_NS) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ARXIV_NS) or "").strip()
        published = entry.findtext("atom:published", default="", namespaces=ARXIV_NS)
        authors = [
            (a.findtext("atom:name", default="", namespaces=ARXIV_NS) or "").strip()
            for a in entry.findall("atom:author", ARXIV_NS)
        ]
        rows.append({
            "id": m.group(1),
            "title": re.sub(r"\s+", " ", title),
            "summary": re.sub(r"\s+", " ", summary),
            "published": published[:10],
            "authors": authors,
        })
    return rows


def gh_open_issue(repo: str, title: str, body: str, labels: list[str]) -> str | None:
    cmd = ["gh", "issue", "create", "-R", repo, "--title", title, "--body", body]
    for label in labels:
        cmd += ["--label", label]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        return (r.stdout.strip().splitlines() or [""])[-1] or None
    except subprocess.CalledProcessError as e:
        print(f"error opening issue: {e.stderr}", file=sys.stderr)
        return None


def ensure_label(repo: str, name: str, color: str, description: str) -> None:
    """Create the label if it doesn't exist; ignore errors if it does."""
    try:
        subprocess.run(
            ["gh", "label", "create", name, "-R", repo, "-c", color, "-d", description],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass


# --- Main -----------------------------------------------------------------

def main() -> int:
    repo = os.environ.get("GH_REPO", "BinWang28/audio-ai-hub")
    dry = bool(int(os.environ.get("DRY_RUN", "0")))
    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    print(f"arxiv-watcher: repo={repo} since={since.date()} dry={dry}", file=sys.stderr)

    have_ids = existing_paper_ids()
    suggested_ids = already_suggested_ids(repo) if not dry else set()
    print(f"already in items/: {len(have_ids)};  already suggested: {len(suggested_ids)}", file=sys.stderr)

    seen: dict[str, dict] = {}
    for cat in CATEGORIES:
        try:
            for p in arxiv_query(cat, since):
                if p["id"] in seen:
                    continue
                # match the audio-ai keyword filter
                if not KEYWORD_RE.search(p["title"] + " " + p["summary"]):
                    continue
                seen[p["id"]] = p
        except Exception as e:
            print(f"warn: arxiv {cat} failed: {e}", file=sys.stderr)
        time.sleep(3)  # be gentle

    # Drop ones we already have or have already suggested
    candidates = [p for p in seen.values() if p["id"] not in have_ids and p["id"] not in suggested_ids]
    # Newest first; cap
    candidates.sort(key=lambda p: p["published"], reverse=True)
    candidates = candidates[:MAX_ISSUES_PER_RUN]
    print(f"new candidates after dedup: {len(candidates)}", file=sys.stderr)
    if not candidates:
        print("nothing new — exiting clean.", file=sys.stderr)
        return 0

    if not dry:
        ensure_label(repo, "auto-suggested", "ededed",
                     "Opened by scripts/arxiv_watcher.py — needs maintainer triage.")
        ensure_label(repo, "new-entry", "1f883d",
                     "Suggests adding a new item.")

    for p in candidates:
        title = f"[Auto-suggested] {p['title'][:160]}"
        authors = ", ".join(p["authors"][:8])
        if len(p["authors"]) > 8:
            authors += ", et al."
        body = (
            f"_Discovered by the weekly arxiv watcher. Triage by editing or closing._\n\n"
            f"- **Paper**: https://arxiv.org/abs/{p['id']}\n"
            f"- **Published**: {p['published']}\n"
            f"- **Authors**: {authors}\n\n"
            f"### Abstract excerpt\n\n"
            f"> {p['summary'][:800]}\n\n"
            f"### Suggested JSON\n\n"
            f"```json\n{{\n"
            f"    \"Category\":     \"Model and Methods\",\n"
            f"    \"Type\":         \"Model\",\n"
            f"    \"Abbreviation\": \"<short name>\",\n"
            f"    \"Title\":        \"{p['title'].replace('\"', '\\\\\"')}\",\n"
            f"    \"Time\":         \"{p['published'][:7]}\",\n"
            f"    \"Affiliation\":  \"\",\n"
            f"    \"Author\":       \"{authors.replace('\"', '\\\\\"')}\",\n"
            f"    \"GitHub_Link\":  \"\",\n"
            f"    \"Paper_Link\":   \"https://arxiv.org/abs/{p['id']}\",\n"
            f"    \"HF_Link\":      \"\",\n"
            f"    \"Demo_Link\":    \"\",\n"
            f"    \"Other_Link\":   \"\",\n"
            f"    \"Audio_Input\":  \"Yes\",\n"
            f"    \"Audio_Output\": \"No\",\n"
            f"    \"Language\":     \"\",\n"
            f"    \"Description\":  \"\"\n"
            f"}}\n```\n\n"
            f"_Not in scope? Close this issue and no further action is needed; the watcher remembers and won't re-suggest._"
        )
        if dry:
            print(f"DRY would open: {title}", file=sys.stderr)
            continue
        url = gh_open_issue(repo, title, body, ["auto-suggested", "new-entry"])
        print(f"opened: {url}  ({p['id']})", file=sys.stderr)
        time.sleep(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
