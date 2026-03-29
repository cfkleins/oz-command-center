#!/usr/bin/env python3
"""
update_command_center.py
Oz's script to gather current operational state and push data.json to GitHub.
GitHub auto-triggers Vercel rebuild. No git required on Oz Box.

Usage:
  python update_command_center.py [--dry-run]

Required environment variables:
  GITHUB_TOKEN   — personal access token with repo write scope
  GITHUB_REPO    — e.g. "cfkleins/oz-command-center"

Optional (enhance with live data):
  OPEN_BRAIN_KEY — Supabase anon key for Open Brain queries
"""

import json
import os
import sys
import base64
import datetime
import argparse
import urllib.request
import urllib.error

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "cfkleins/oz-command-center")
DATA_FILE    = "data.json"

# Staleness thresholds (seconds)
THRESHOLD_OZ_STATUS   = 7200    # 2 hours
THRESHOLD_BRIEF       = 604800  # 7 days
THRESHOLD_PROJECTS    = 86400   # 24 hours


# ─────────────────────────────────────────
# GITHUB API
# ─────────────────────────────────────────

def github_get(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    })
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def github_put(path, payload):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28"
    })
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def push_data_json(payload_dict, dry_run=False):
    """Push data.json to GitHub, updating in-place (preserves git history)."""
    content_bytes = json.dumps(payload_dict, indent=2, ensure_ascii=False).encode()
    content_b64   = base64.b64encode(content_bytes).decode()

    # Get current SHA (required for update)
    sha = None
    try:
        existing = github_get(f"contents/{DATA_FILE}")
        sha = existing.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # File doesn't exist yet — first push, no SHA needed

    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    commit_msg = f"oz: update data.json {now_str}"

    body = {
        "message": commit_msg,
        "content": content_b64,
    }
    if sha:
        body["sha"] = sha

    if dry_run:
        print("[DRY RUN] Would push data.json with commit:", commit_msg)
        print(json.dumps(payload_dict, indent=2)[:500], "...")
        return

    result = github_put(f"contents/{DATA_FILE}", body)
    print(f"✓ data.json pushed — commit {result['commit']['sha'][:7]}")


# ─────────────────────────────────────────
# DATA GATHERING
# ─────────────────────────────────────────

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def check_gateway():
    """Ping OpenClaw gateway. Returns status string."""
    try:
        # Simple HTTP check on the gateway health endpoint
        req = urllib.request.Request(
            "http://127.0.0.1:18789/health",
            headers={"Accept": "application/json"}
        )
        req.get_method = lambda: "GET"
        with urllib.request.urlopen(req, timeout=3) as r:
            body = json.loads(r.read())
            if body.get("ok") or body.get("status") == "live":
                return "ONLINE"
            return "DEGRADED"
    except Exception:
        return "OFFLINE"


def get_open_brain_stats():
    """Query Open Brain thought stats. Returns dict or None."""
    key = os.environ.get("OPEN_BRAIN_KEY", "")
    if not key:
        return None
    try:
        url = "https://fdbkwkrtdeumvpwmvoev.supabase.co/functions/v1/open-brain-mcp"
        payload = json.dumps({"action": "thought_stats"}).encode()
        req = urllib.request.Request(url, data=payload, method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        })
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def load_current_data_json():
    """Load existing data.json from GitHub to preserve fields Oz doesn't update."""
    try:
        existing = github_get(f"contents/{DATA_FILE}")
        raw = base64.b64decode(existing["content"]).decode()
        return json.loads(raw)
    except Exception:
        return {}


# ─────────────────────────────────────────
# BUILD PAYLOAD
# ─────────────────────────────────────────

def build_payload(current):
    """Merge current state with fresh Oz readings."""
    now = now_iso()

    # Gateway check
    gw_status = check_gateway()

    # Open Brain stats (optional live data)
    ob_stats = get_open_brain_stats()
    ob_thoughts = ob_stats.get("total", current.get("ozStatus", {}).get("openBrain", {}).get("totalThoughts", 0)) \
        if ob_stats else current.get("ozStatus", {}).get("openBrain", {}).get("totalThoughts", 0)

    # Preserve fields from current that Oz doesn't touch this run
    current_brief   = current.get("missionBrief", {})
    current_daily   = current.get("dailyBrief", {})
    current_projects = current.get("projects", [])
    current_oz      = current.get("ozStatus", {})

    # Build updated daily brief
    daily_brief = {
        "lastUpdated": now,
        "date": datetime.datetime.now().strftime("%A, %B %-d, %Y"),
        "time": datetime.datetime.now().strftime("%-I:%M %p PT"),
        "infrastructureStatus": (
            f"Gateway: {gw_status}. Open Brain: {ob_thoughts} thoughts. "
            + ("All systems green." if gw_status == "ONLINE" else "Gateway offline — check OpenClaw.")
        ),
        "openItems": current_daily.get("openItems", []),
        "standingCommitments": current_daily.get("standingCommitments", [
            "Cron schedule DISABLED — standing order",
            "Session checkpoint every close — mandatory",
            "Sunday review 7PM — weekly cadence"
        ])
    }

    # Build Oz status (preserve most fields, update live ones)
    oz_status = dict(current_oz)
    oz_status["lastUpdated"] = now
    if "gateway" in oz_status:
        oz_status["gateway"]["status"] = gw_status
    if "openBrain" in oz_status:
        oz_status["openBrain"]["totalThoughts"] = ob_thoughts

    return {
        "_meta": {
            "version": current.get("_meta", {}).get("version", "1.0.0"),
            "lastUpdated": now,
            "updatedBy": "oz-update-script",
            "nextExpected": (
                datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            "stalenessThresholds": {
                "ozStatus": THRESHOLD_OZ_STATUS,
                "missionBrief": THRESHOLD_BRIEF,
                "projects": THRESHOLD_PROJECTS
            }
        },
        "ozStatus": oz_status,
        "missionBrief": current_brief,   # Marcus writes this — Oz preserves it
        "dailyBrief": daily_brief,
        "projects": current_projects      # Oz can update these; preserved here
    }


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Update OZ Command Center data.json")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without pushing")
    args = parser.parse_args()

    if not GITHUB_TOKEN and not args.dry_run:
        print("ERROR: GITHUB_TOKEN environment variable not set.")
        print("Export it: export GITHUB_TOKEN=ghp_...")
        sys.exit(1)

    print(f"OZ Command Center updater — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Repo: {GITHUB_REPO}")

    # Load current data.json from GitHub (to preserve fields)
    print("Loading current data.json from GitHub...")
    current = load_current_data_json()

    # Build updated payload
    print("Gathering live state...")
    payload = build_payload(current)

    # Push
    push_data_json(payload, dry_run=args.dry_run)

    if not args.dry_run:
        print("Vercel will auto-deploy from the GitHub push.")
        print(f"Dashboard: https://oz-command-center.vercel.app")


if __name__ == "__main__":
    main()
