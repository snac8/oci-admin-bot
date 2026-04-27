#!/usr/bin/env python3
"""
dev2_refresh.py
Manages quarterly Oracle Fusion dev2 environment refresh:
  - 1 week before the 15th of each quarter start month: Slack reminder + submit OCI refresh
  - Day before refresh at 9am PT: 24-hour Slack notice

Weekend rule: if the target date (15th or 8th) falls on Sat/Sun, action
is taken on the Thursday of that week instead.

Quarter start months: January, April, July, October.

Run modes (set via RUN_MODE env var or --mode argument):
  check-reminder  : post 1-week reminder + submit OCI refresh if today is the right day
  check-notify    : post 24-hour notice if today is the right day (run at 9am PT)
  force-reminder  : always post the 1-week reminder + submit OCI refresh (for testing)
  force-submit    : always submit the refresh only (for testing, pass --dry-run to skip OCI call)
  force-notify    : always post the 24-hour notification (for testing)
"""

import os
import sys
import json
import argparse
import subprocess
from datetime import date, datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
import requests

# --- Config ---
SLACK_BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL     = os.environ.get("SLACK_CHANNEL", "#test-ai")
DEV2_OCID         = os.environ.get("DEV2_OCID",  "ocid1.fusionenvironment.oc1.iad.aaaaaaaa3634hbx5ae7c2xi2sgjki47iytboyenewerpxizsq2ijwkjdlldq")
PROD_OCID         = os.environ.get("PROD_OCID",  "ocid1.fusionenvironment.oc1.iad.aaaaaaaanw7ctvqo7mmgwd36qnljr7j4p5ll2y4eaty4i6726b2thvg7orda")
DEV2_URL          = os.environ.get("DEV2_URL",   "https://ehsg-dev2.login.us6.oraclecloud.com/")
OCI_NAMESPACE     = "axbix6knqxie"
OCI_BUCKET        = "fbs-admin-state"
STATE_OBJECT      = "dev2_refresh_state.json"
STATE_FILE        = "/tmp/dev2_refresh_state.json"
QUARTER_MONTHS    = {1, 4, 7, 10}  # Jan, Apr, Jul, Oct
REFRESH_DAY       = 15
REMINDER_DAYS_BEFORE = 7


# ---------------------------------------------------------------------------
# Date logic
# ---------------------------------------------------------------------------

def effective_date(target: date) -> date:
    """If target falls on Sat/Sun, return the Thursday before."""
    if target.weekday() == 5:   # Saturday
        return target - timedelta(days=2)
    if target.weekday() == 6:   # Sunday
        return target - timedelta(days=3)
    return target


def this_quarters_dates(ref: date = None) -> tuple:
    """
    Returns (reminder_date, refresh_date) for the current or upcoming quarter.
    Both are already weekend-adjusted.
    """
    today = ref or date.today()
    # Find the current or next quarter start month
    for month_offset in range(12):
        d = today + relativedelta(months=month_offset)
        if d.month in QUARTER_MONTHS:
            refresh_raw  = date(d.year, d.month, REFRESH_DAY)
            reminder_raw = refresh_raw - timedelta(days=REMINDER_DAYS_BEFORE)
            if refresh_raw >= today:
                return effective_date(reminder_raw), effective_date(refresh_raw)
    return None, None


def is_today(target: date) -> bool:
    return target == date.today()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_scheduled_start(refresh_date: date) -> str:
    """Return ISO 8601 string for 5pm PT on refresh_date."""
    return f"{refresh_date.isoformat()}T17:00:00-07:00"


def format_scheduled_time(iso: str) -> str:
    """Format ISO timestamp → 'July 15, 2026 at 5:00 AM PT'"""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%B %-d, %Y at %-I:%M %p PT")
    except Exception:
        return iso


def save_refresh_state(channel_id: str, thread_ts: str, scheduled_start: str, work_request_id: str = ""):
    """Persist refresh state to OCI Object Storage so the monitor can find the thread."""
    state = {
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "scheduled_start": scheduled_start,
        "work_request_id": work_request_id,
        "last_processed_ts": "0",
        "status": "scheduled",
        "target_ocid": DEV2_OCID,
        "target_url": DEV2_URL,
        "watched_threads": [{"channel_id": channel_id, "thread_ts": thread_ts, "last_processed_ts": "0"}],
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    result = subprocess.run([
        "oci", "os", "object", "put",
        "--namespace", OCI_NAMESPACE,
        "--bucket-name", OCI_BUCKET,
        "--name", STATE_OBJECT,
        "--file", STATE_FILE,
        "--force",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: could not save state to OCI: {result.stderr}")
    else:
        print("Refresh state saved to OCI Object Storage.")


# ---------------------------------------------------------------------------
# OCI refresh
# ---------------------------------------------------------------------------

def submit_refresh(scheduled_date: date, dry_run: bool = False) -> dict:
    """Submit OCI Fusion environment refresh from prod → dev2, scheduled for a specific date."""
    scheduled_start = make_scheduled_start(scheduled_date)
    cmd = [
        "oci", "fusion-apps",
        "create-refresh-activity-details", "create-refresh-activity",
        "--fusion-environment-id", DEV2_OCID,
        "--source-fusion-environment-id", PROD_OCID,
        "--is-data-masking-opted", "false",
        "--time-scheduled-start", scheduled_start,
        "--region", "us-ashburn-1",
    ]
    if dry_run:
        print(f"[DRY RUN] Would run: {' '.join(cmd)}")
        return {"status": "dry-run", "scheduled_start": scheduled_start}

    print(f"Submitting refresh: dev2 ← prod (scheduled {scheduled_start}) ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR submitting refresh:\n{result.stderr}")
        sys.exit(1)

    data = json.loads(result.stdout)
    data["scheduled_start"] = scheduled_start
    print(f"Refresh submitted: {json.dumps(data, indent=2)}")
    return data


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def _post_slack(blocks: list) -> tuple:
    """Post blocks to Slack. Returns (channel_id, message_ts)."""
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN not set")
        sys.exit(1)
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"ERROR: Slack API: {data.get('error')}")
        sys.exit(1)
    print(f"Posted to {SLACK_CHANNEL} (ts={data.get('ts')})")
    return data.get("channel"), data.get("ts")


def post_reminder(refresh_date: date, scheduled_start: str, work_request_id: str = "") -> tuple:
    """1-week advance notice. Returns (channel_id, ts)."""
    body = (
        f"Dev URL: <{DEV2_URL}|{DEV2_URL}>\n"
        f"Scheduled Date/Time: {format_scheduled_time(scheduled_start)}"
    )
    if work_request_id:
        body += f"\nRequest ID: `{work_request_id}`"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Reminder - Below Oracle Environment will be refreshed on this date"}
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
    ]
    return _post_slack(blocks)


def post_submitted(refresh_date: date, scheduled_start: str, work_request_id: str = ""):
    """24-hour advance notice the day before refresh."""
    body = (
        f"URL: <{DEV2_URL}|{DEV2_URL}>\n"
        f"Scheduled Date/Time: {format_scheduled_time(scheduled_start)}"
    )
    if work_request_id:
        body += f"\nRequest ID: `{work_request_id}`"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Reminder - Below Oracle Environment is planned to be refreshed in next 24 hours"}
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
    ]
    _post_slack(blocks)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.environ.get("RUN_MODE", "check-reminder"),
                        choices=["check-reminder", "check-notify",
                                 "force-reminder", "force-submit", "force-notify", "force-test"])
    parser.add_argument("--dry-run", action="store_true", help="Skip actual OCI API call")
    args = parser.parse_args()

    reminder_date, refresh_date = this_quarters_dates()
    print(f"Next reminder date : {reminder_date}")
    print(f"Next refresh date  : {refresh_date}")
    print(f"Today              : {date.today()}")
    print(f"Mode               : {args.mode}")

    scheduled_start = make_scheduled_start(refresh_date)

    if args.mode == "check-reminder":
        if is_today(reminder_date):
            data = submit_refresh(refresh_date, dry_run=args.dry_run)
            wrid = data.get("opc-work-request-id", "")
            channel_id, ts = post_reminder(refresh_date, scheduled_start, work_request_id=wrid)
            if not args.dry_run and channel_id and ts:
                save_refresh_state(channel_id, ts, scheduled_start, work_request_id=wrid)
        else:
            print("Not reminder day — nothing to do.")

    elif args.mode == "check-notify":
        # Load actual scheduled_start from OCI state (may have been rescheduled)
        state_scheduled_start = scheduled_start
        state_wrid = ""
        try:
            result = subprocess.run([
                "oci", "os", "object", "get",
                "--namespace", OCI_NAMESPACE,
                "--bucket-name", OCI_BUCKET,
                "--name", STATE_OBJECT,
                "--file", STATE_FILE,
            ], capture_output=True, text=True)
            if result.returncode == 0:
                with open(STATE_FILE) as f:
                    st = json.load(f)
                    state_scheduled_start = st.get("scheduled_start", scheduled_start)
                    state_wrid = st.get("work_request_id", "")
        except Exception:
            pass

        # Derive notify_date from the actual scheduled date (may differ from quarter default)
        from datetime import datetime as _dt
        actual_refresh_date = _dt.fromisoformat(state_scheduled_start).date()
        notify_date = actual_refresh_date - timedelta(days=1)
        if is_today(notify_date):
            post_submitted(actual_refresh_date, state_scheduled_start, work_request_id=state_wrid)
        else:
            print(f"Not notify day (expected {notify_date}) — nothing to do.")

    elif args.mode == "force-reminder":
        data = submit_refresh(refresh_date, dry_run=args.dry_run)
        wrid = data.get("opc-work-request-id", "")
        channel_id, ts = post_reminder(refresh_date, scheduled_start, work_request_id=wrid)
        if not args.dry_run and channel_id and ts:
            save_refresh_state(channel_id, ts, scheduled_start, work_request_id=wrid)

    elif args.mode == "force-submit":
        data = submit_refresh(refresh_date, dry_run=args.dry_run)

    elif args.mode == "force-notify":
        try:
            with open(STATE_FILE) as f:
                wrid = json.load(f).get("work_request_id", "")
        except Exception:
            wrid = ""
        post_submitted(refresh_date, scheduled_start, work_request_id=wrid)

    elif args.mode == "force-test":
        # Submit OCI refresh once, then post both reminders with the same request ID.
        # State is saved from the 1-week reminder thread (monitor watches that thread).
        data = submit_refresh(refresh_date, dry_run=args.dry_run)
        wrid = data.get("opc-work-request-id", "")
        print(f"\n--- Posting 1-week reminder ---")
        channel_id, ts = post_reminder(refresh_date, scheduled_start, work_request_id=wrid)
        if not args.dry_run and channel_id and ts:
            save_refresh_state(channel_id, ts, scheduled_start, work_request_id=wrid)
        print(f"\n--- Posting 24-hour notice ---")
        post_submitted(refresh_date, scheduled_start, work_request_id=wrid)


if __name__ == "__main__":
    main()
