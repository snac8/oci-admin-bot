#!/usr/bin/env python3
"""
maintenance_reminder.py
Checks all Oracle Fusion environments for upcoming quarterly upgrades.
If a QUARTERLY_UPGRADE MAINTENANCE activity starts within the next 7 days,
posts a Slack reminder listing all affected environments and the maintenance window.

Run modes (--mode or RUN_MODE env var):
  check   : post reminder only if maintenance is within 7 days (default)
  force   : always post reminder regardless of timing (for testing)
"""

import os
import re
import sys
import json
import argparse
import subprocess
from datetime import datetime, timezone, timedelta

import requests

# --- Config ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL   = os.environ.get("SLACK_CHANNEL", "#test-ai")
OCI_REGION      = "us-ashburn-1"
REMINDER_DAYS   = 7

KNOWN_ENVIRONMENTS = {
    "prod":  (os.environ.get("PROD_OCID",  "ocid1.fusionenvironment.oc1.iad.aaaaaaaanw7ctvqo7mmgwd36qnljr7j4p5ll2y4eaty4i6726b2thvg7orda"),
              "https://ehsg.fa.us6.oraclecloud.com/fscmUI/faces/FuseOverview"),
    "dev1":  (os.environ.get("DEV1_OCID",  "ocid1.fusionenvironment.oc1.iad.aaaaaaaatpfxad2y7je7shaaxnxqkdsaf4qfqi4occjl43vuo3xdgus6q2na"),
              "https://ehsg-dev1.login.us6.oraclecloud.com/"),
    "dev2":  (os.environ.get("DEV2_OCID",  "ocid1.fusionenvironment.oc1.iad.aaaaaaaa3634hbx5ae7c2xi2sgjki47iytboyenewerpxizsq2ijwkjdlldq"),
              "https://ehsg-dev2.login.us6.oraclecloud.com/"),
    "dev3":  (os.environ.get("DEV3_OCID",  "ocid1.fusionenvironment.oc1.iad.aaaaaaaackbsvsusvkt7zcaeqtxrbax6iqhg6xm3hh5a6lguo4jvczqtbozq"),
              "https://ehsg-dev3.fa.ocs.oraclecloud.com/fscmUI/faces/FuseOverview"),
    "dev4":  (os.environ.get("DEV4_OCID",  "ocid1.fusionenvironment.oc1.iad.aaaaaaaaodjkbol5peswdwltoo6kpgsu7iy7srda2qqz2xtfns7luqptmwwq"),
              "https://ehsg-dev4.fa.ocs.oraclecloud.com/fscmUI/faces/FuseOverview"),
    "test":  (os.environ.get("TEST_OCID",  "ocid1.fusionenvironment.oc1.iad.aaaaaaaa5qufewyyuu4gxjwd6z3ppixw74jsg6vscnwzumtcifdf6ryrxfrq"),
              "https://ehsg-test.fa.us6.oraclecloud.com/fscmUI/faces/FuseOverview"),
}


def get_upcoming_maintenance(ocid: str) -> dict | None:
    """
    Query scheduled activities for an environment.
    Returns the MAINTENANCE-phase QUARTERLY_UPGRADE activity that is upcoming
    (state ACCEPTED or SCHEDULED), or None.
    """
    result = subprocess.run(
        [
            "oci", "fusion-apps", "scheduled-activity", "list",
            "--fusion-environment-id", ocid,
            "--region", OCI_REGION,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  WARNING: OCI call failed for {ocid}: {result.stderr.strip()}")
        return None

    try:
        items = json.loads(result.stdout).get("data", {}).get("items", [])
    except Exception as e:
        print(f"  WARNING: could not parse OCI response for {ocid}: {e}")
        return None

    now = datetime.now(timezone.utc)
    best = None
    for item in items:
        if item.get("lifecycle-state") not in ("ACCEPTED", "SCHEDULED"):
            continue
        if item.get("scheduled-activity-phase") != "MAINTENANCE":
            continue
        actions = item.get("actions", [])
        if not any(a.get("action-type") == "QUARTERLY_UPGRADE" for a in actions):
            continue
        start_str = item.get("time-scheduled-start", "")
        try:
            start_dt = datetime.fromisoformat(start_str)
        except Exception:
            continue
        if start_dt < now:
            continue
        raw_desc = next(
            (a.get("description", "") for a in actions if a.get("action-type") == "QUARTERLY_UPGRADE"),
            "",
        )
        # Strip " and the <Month> Maintenance Pack" suffix
        description = re.sub(r'\s+and the \w+ Maintenance Pack\s*$', '', raw_desc, flags=re.IGNORECASE).strip()
        entry = {
            "start": start_dt,
            "finish": item.get("time-expected-finish", ""),
            "description": description,
        }
        # Pick the soonest
        if best is None or start_dt < best["start"]:
            best = entry

    return best


def format_pt(dt: datetime) -> str:
    """Convert UTC datetime to PT (PDT Apr–Oct, PST otherwise) and format."""
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    # PDT = UTC-7 (Mar second Sun – Nov first Sun); PST = UTC-8 otherwise
    is_pdt = 4 <= dt.month <= 10
    offset = timedelta(hours=-7) if is_pdt else timedelta(hours=-8)
    label  = "PDT" if is_pdt else "PST"
    dt_pt  = dt.astimezone(timezone(offset))
    return dt_pt.strftime(f"%a, %b %-d, %Y, %-I:%M %p {label}")


def post_slack_reminder(maintenance_dt: datetime, description: str, affected: list[tuple[str, str]]) -> None:
    """Post maintenance reminder to Slack."""
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN not set")
        sys.exit(1)

    header = "Reminder - Quarterly Oracle Fusion Upgrade"
    body_lines = [
        f"*Maintenance window:* {format_pt(maintenance_dt)}",
        f"*Update:* {description}",
        "",
        "*Affected environments:*",
    ]
    for env_name, url in affected:
        body_lines.append(f"• {env_name.upper()}: <{url}|{url}>")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(body_lines)},
        },
    ]

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"ERROR: Slack API: {data.get('error')}")
        sys.exit(1)
    print(f"Posted maintenance reminder to {SLACK_CHANNEL} (ts={data.get('ts')})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        default=os.environ.get("RUN_MODE", "check"),
        choices=["check", "force"],
    )
    args = parser.parse_args()

    print(f"Mode: {args.mode}")
    print(f"Checking {len(KNOWN_ENVIRONMENTS)} environments for upcoming quarterly upgrades...\n")

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=REMINDER_DAYS)

    # Collect upcoming maintenance per environment
    # Group environments that share the same maintenance window
    env_results: dict[str, tuple] = {}  # env_name → (start_dt, finish, description, url)

    for env_name, (ocid, url) in KNOWN_ENVIRONMENTS.items():
        print(f"  Checking {env_name} ({ocid[:40]}...)...")
        info = get_upcoming_maintenance(ocid)
        if info:
            env_results[env_name] = (info["start"], info["finish"], info["description"], url)
            print(f"    → maintenance on {format_pt(info['start'])}")
        else:
            print(f"    → no upcoming quarterly upgrade found")

    if not env_results:
        print("\nNo upcoming quarterly upgrades found across any environment.")
        return

    # Find the soonest maintenance window
    soonest_dt = min(v[0] for v in env_results.values())
    description = next(v[2] for v in env_results.values() if v[0] == soonest_dt)

    # Environments that share this maintenance window (within 1 hour)
    affected = [
        (name, data[3])
        for name, data in sorted(env_results.items())
        if abs((data[0] - soonest_dt).total_seconds()) < 3600
    ]

    days_away = (soonest_dt - now).days
    print(f"\nSoonest maintenance: {format_pt(soonest_dt)} ({days_away} days away)")
    print(f"Affected envs: {[e[0] for e in affected]}")

    if args.mode == "force" or soonest_dt <= cutoff:
        print(f"\nPosting reminder to {SLACK_CHANNEL}...")
        post_slack_reminder(soonest_dt, description, affected)
    else:
        print(f"\nMaintenance is {days_away} days away (>{REMINDER_DAYS} days) — no reminder needed today.")


if __name__ == "__main__":
    main()
