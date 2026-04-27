#!/usr/bin/env python3
"""
slack_refresh_monitor.py
Runs every 15 minutes. Does three things:

1. Channel scan — looks for NEW messages in the channel that @mention the bot.
   Handles refresh requests (schedules OCI refresh) and status queries.

2. Active thread monitor — if a refresh is currently scheduled:
   - Checks OCI for completion (SUCCEEDED) and posts a Slack notification.
   - Reads thread replies for @bot commands: cancel, reschedule, status.

3. Auto-discovery — scans ALL known environments for scheduled refresh activities,
   regardless of whether they were submitted via this bot or the OCI UI directly.
   Posts 1-week and 24-hour reminders automatically.

Supported channel command:
  @fbs-admin refresh <url> on <date> [<time>] [PT]
  @fbs-admin when is the next refresh of dev1?
  @fbs-admin what's scheduled for dev2?

Supported thread commands (must @mention bot):
  @fbs-admin cancel
  @fbs-admin reschedule to <date> <time> PT
  @fbs-admin cancel and reschedule to <date> <time> PT
  @fbs-admin status / when is this / what's scheduled
"""

import os
import sys
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

import requests
from dateutil import parser as dateutil_parser

# --- Config ---
SLACK_BOT_TOKEN  = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL    = os.environ.get("SLACK_CHANNEL", "#test-ai")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "C0ALU5462EB")
PROD_OCID = os.environ.get("PROD_OCID", "ocid1.fusionenvironment.oc1.iad.aaaaaaaanw7ctvqo7mmgwd36qnljr7j4p5ll2y4eaty4i6726b2thvg7orda")

# Map keyword → (ocid, display_url)
# dev1=ehsg-dev1, dev2=ehsg-dev2, dev3=ehsg-proj-dev1, dev4=ehsg-proj-test, test=ehsg-test
KNOWN_ENVIRONMENTS = {
    "dev1": (
        os.environ.get("DEV1_OCID", "ocid1.fusionenvironment.oc1.iad.aaaaaaaatpfxad2y7je7shaaxnxqkdsaf4qfqi4occjl43vuo3xdgus6q2na"),
        "https://ehsg-dev1.login.us6.oraclecloud.com/",
    ),
    "dev2": (
        os.environ.get("DEV2_OCID", "ocid1.fusionenvironment.oc1.iad.aaaaaaaa3634hbx5ae7c2xi2sgjki47iytboyenewerpxizsq2ijwkjdlldq"),
        "https://ehsg-dev2.login.us6.oraclecloud.com/",
    ),
    "dev3": (
        os.environ.get("DEV3_OCID", "ocid1.fusionenvironment.oc1.iad.aaaaaaaackbsvsusvkt7zcaeqtxrbax6iqhg6xm3hh5a6lguo4jvczqtbozq"),
        "https://ehsg-dev3.fa.ocs.oraclecloud.com/fscmUI/faces/FuseOverview",
    ),
    "dev4": (
        os.environ.get("DEV4_OCID", "ocid1.fusionenvironment.oc1.iad.aaaaaaaaodjkbol5peswdwltoo6kpgsu7iy7srda2qqz2xtfns7luqptmwwq"),
        "https://ehsg-dev4.fa.ocs.oraclecloud.com/fscmUI/faces/FuseOverview",
    ),
    "test": (
        os.environ.get("TEST_OCID", "ocid1.fusionenvironment.oc1.iad.aaaaaaaa5qufewyyuu4gxjwd6z3ppixw74jsg6vscnwzumtcifdf6ryrxfrq"),
        "https://ehsg-test.fa.us6.oraclecloud.com/fscmUI/faces/FuseOverview",
    ),
}

DEV2_OCID = KNOWN_ENVIRONMENTS["dev2"][0]
DEV2_URL  = KNOWN_ENVIRONMENTS["dev2"][1]
OCI_NAMESPACE  = "axbix6knqxie"
OCI_BUCKET     = "fbs-admin-state"
STATE_OBJECT   = "dev2_refresh_state.json"
STATE_FILE     = "/tmp/dev2_refresh_state.json"
OCI_REGION     = "us-ashburn-1"   # Fusion Apps live here
DEFAULT_HOUR_PT = 17  # 5:00 PM PT if no time is specified


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_time(iso: str) -> str:
    """ISO timestamp → 'July 15, 2026 at 5:00 PM PT'"""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo in (timezone.utc, None):
            offset = timedelta(hours=-7) if 4 <= dt.month <= 10 else timedelta(hours=-8)
            dt = dt.astimezone(timezone(offset))
        return dt.strftime("%B %-d, %Y at %-I:%M %p PT")
    except Exception:
        return iso


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------

def _slack_get(url, params):
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params=params,
        timeout=10,
    )
    return resp.json()


def get_bot_user_id() -> str:
    data = _slack_get("https://slack.com/api/auth.test", {})
    if not data.get("ok"):
        print(f"ERROR: auth.test failed: {data.get('error')}")
        sys.exit(1)
    return data["user_id"]


def get_channel_messages(channel_id: str, oldest: str = "0") -> list:
    """Fetch new top-level channel messages (not thread replies)."""
    data = _slack_get("https://slack.com/api/conversations.history", {
        "channel": channel_id,
        "oldest": oldest,
        "limit": 50,
    })
    if not data.get("ok"):
        print(f"ERROR fetching channel history: {data.get('error')}")
        return []
    # Exclude thread replies (they have thread_ts != ts)
    return [m for m in data.get("messages", [])
            if m.get("ts") == m.get("thread_ts", m.get("ts"))]


def get_thread_replies(channel_id: str, thread_ts: str, oldest: str = "0") -> list:
    data = _slack_get("https://slack.com/api/conversations.replies", {
        "channel": channel_id,
        "ts": thread_ts,
        "oldest": oldest,
        "limit": 100,
    })
    if not data.get("ok"):
        print(f"ERROR fetching thread replies: {data.get('error')}")
        return []
    messages = data.get("messages", [])
    return [m for m in messages if m.get("ts") != thread_ts]


def post_message(channel_id: str, blocks: list, thread_ts: str = None) -> tuple:
    payload = {"channel": channel_id, "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"ERROR posting message: {data.get('error')}")
    return data.get("channel"), data.get("ts")


def post_thread_reply(channel_id: str, thread_ts: str, text: str):
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json={"channel": channel_id, "thread_ts": thread_ts, "text": text},
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"ERROR posting thread reply: {data.get('error')}")


def post_completion_notification(channel_id: str, thread_ts: str,
                                  time_finished: str, scheduled_start: str,
                                  activity_id: str, env_url: str = None):
    url = env_url or DEV2_URL
    finished_fmt  = format_time(time_finished)
    scheduled_fmt = format_time(scheduled_start)
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Oracle Environment Refresh Completed"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"URL: <{url}|{url}>\n"
                f"Scheduled Date/Time: {scheduled_fmt}\n"
                f"Completed Date/Time: {finished_fmt}\n"
                f"Activity ID: `{activity_id}`"
            )}
        },
    ]
    post_message(channel_id, blocks)
    if thread_ts:
        post_thread_reply(channel_id, thread_ts,
            f"Refresh completed on {finished_fmt}.\n"
            f"URL: {url}\n"
            f"Activity ID: `{activity_id}`")


# ---------------------------------------------------------------------------
# OCI helpers
# ---------------------------------------------------------------------------

def get_activity(activity_id: str, env_ocid: str = None) -> dict | None:
    result = subprocess.run([
        "oci", "fusion-apps", "refresh-activity", "get",
        "--fusion-environment-id", env_ocid or DEV2_OCID,
        "--refresh-activity-id", activity_id,
        "--region", OCI_REGION,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR getting activity {activity_id}: {result.stderr}")
        return None
    return json.loads(result.stdout).get("data")


def list_all_activities(env_ocid: str = None) -> list:
    result = subprocess.run([
        "oci", "fusion-apps", "refresh-activity", "list",
        "--fusion-environment-id", env_ocid or DEV2_OCID,
        "--region", OCI_REGION,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR listing activities: {result.stderr}")
        return []
    return json.loads(result.stdout).get("data", {}).get("items", [])


def list_active_activities(env_ocid: str = None) -> list:
    active_states = {"ACCEPTED", "SCHEDULED", "IN_PROGRESS"}
    return [a for a in list_all_activities(env_ocid) if a.get("lifecycle-state") in active_states]


def find_activity_id(scheduled_start_iso: str, env_ocid: str = None) -> str | None:
    try:
        target_dt = datetime.fromisoformat(scheduled_start_iso).astimezone(timezone.utc)
    except Exception:
        target_dt = None

    terminal = {"DELETED", "CANCELLED", "FAILED"}
    activities = list_all_activities(env_ocid)

    if target_dt:
        for act in activities:
            ts = act.get("time-scheduled-start", "")
            try:
                act_dt = datetime.fromisoformat(ts).astimezone(timezone.utc)
                if abs((act_dt - target_dt).total_seconds()) < 120:
                    return act["id"]
            except Exception:
                continue

    non_terminal = [a for a in activities if a.get("lifecycle-state") not in terminal]
    if non_terminal:
        latest = max(non_terminal, key=lambda a: a.get("time-accepted", ""))
        return latest["id"]
    return None


def cancel_activity(activity_id: str, env_ocid: str = None) -> bool:
    result = subprocess.run([
        "oci", "fusion-apps", "refresh-activity", "delete",
        "--fusion-environment-id", env_ocid or DEV2_OCID,
        "--refresh-activity-id", activity_id,
        "--region", OCI_REGION,
        "--force",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR cancelling activity {activity_id}: {result.stderr}")
        return False
    print(f"Cancelled activity: {activity_id}")
    return True


def submit_refresh(scheduled_start: str, target_ocid: str = None) -> dict:
    env_ocid = target_ocid or DEV2_OCID
    result = subprocess.run([
        "oci", "fusion-apps",
        "create-refresh-activity-details", "create-refresh-activity",
        "--fusion-environment-id", env_ocid,
        "--source-fusion-environment-id", PROD_OCID,
        "--is-data-masking-opted", "false",
        "--time-scheduled-start", scheduled_start,
        "--region", OCI_REGION,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR submitting refresh: {result.stderr}")
        return {}
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    result = subprocess.run([
        "oci", "os", "object", "get",
        "--namespace", OCI_NAMESPACE,
        "--bucket-name", OCI_BUCKET,
        "--name", STATE_OBJECT,
        "--file", STATE_FILE,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def add_watched_thread(state: dict, channel_id: str, thread_ts: str) -> dict:
    """Add a thread to the list of threads the monitor watches for commands."""
    watched = state.get("watched_threads", [])
    entry = {"channel_id": channel_id, "thread_ts": thread_ts, "last_processed_ts": "0"}
    if not any(t["thread_ts"] == thread_ts for t in watched):
        watched.append(entry)
    state["watched_threads"] = watched
    return state


def save_state(state: dict):
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
        print(f"WARNING: could not save state: {result.stderr}")


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

def extract_url_from_slack_text(text: str) -> str | None:
    """Extract URL from Slack's <url|label> or <url> encoding."""
    m = re.search(r'<(https?://[^|>]+)(?:\|[^>]*)?>', text)
    return m.group(1) if m else None


def resolve_env_from_text(text: str) -> tuple:
    """
    Detect which environment is mentioned in the text.
    Returns (env_name, ocid, url), defaulting to dev2 if none found.
    """
    clean = re.sub(r'<[^>]+>', ' ', text).lower()
    clean = re.sub(r'@[\w-]+', ' ', clean)
    url = extract_url_from_slack_text(text)
    for env_name, (ocid, env_url) in KNOWN_ENVIRONMENTS.items():
        url_match  = url and env_name in url.lower()
        text_match = re.search(rf'\b{re.escape(env_name)}\b', clean)
        if url_match or text_match:
            return env_name, ocid, env_url
    return "dev2", DEV2_OCID, DEV2_URL


def is_status_query(text: str) -> bool:
    """Return True if the message is asking about scheduled refresh status."""
    t = text.lower()
    return bool(
        re.search(r'\bwhen\b', t) or
        re.search(r'\bstatus\b', t) or
        re.search(r'\bnext\s+refresh\b', t) or
        (re.search(r'\bwhat\b', t) and re.search(r'\bscheduled\b', t)) or
        re.search(r'\bshow\b.*\brefresh\b', t) or
        re.search(r'\blist\b.*\brefresh\b', t)
    )


def parse_datetime_string(raw: str) -> tuple:
    """
    Parse a date/time string. Returns (iso_string, time_was_specified).
    Defaults to DEFAULT_HOUR_PT (5pm PT) if no time given.
    """
    # Strip trailing timezone label
    raw_no_tz = re.sub(r'\s+(pt|pst|pdt)\s*$', '', raw, flags=re.IGNORECASE).strip()
    # Normalise "3.30pm" → "3:30pm"
    raw_no_tz = re.sub(r'(\d)\.(\d{2})', r'\1:\2', raw_no_tz)
    # Add space between digit and am/pm so dateutil can parse "5pm" → "5 pm"
    raw_no_tz = re.sub(r'(\d)(am|pm)', r'\1 \2', raw_no_tz, flags=re.IGNORECASE)

    # Parse with a zeroed-out time default so we can detect if time was omitted
    default_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    dt = dateutil_parser.parse(raw_no_tz, default=default_dt)

    time_specified = not (dt.hour == 0 and dt.minute == 0 and dt.second == 0)
    if not time_specified:
        dt = dt.replace(hour=DEFAULT_HOUR_PT, minute=0, second=0)

    offset = timedelta(hours=-7) if 4 <= dt.month <= 10 else timedelta(hours=-8)
    dt = dt.replace(tzinfo=timezone(offset))
    return dt.isoformat(), time_specified


def parse_channel_command(text: str) -> dict | None:
    """
    Parse a channel-level refresh request. Returns
    {'url': ..., 'scheduled_start': ..., 'time_specified': bool} or None.
    """
    url   = extract_url_from_slack_text(text)
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = re.sub(r'@[\w-]+', ' ', clean)

    noise = (r'\b(fbs.?admin|refresh|schedule[d]?|restart|please|a|an|the|'
             r'on|for|at|to|dev1|dev2|dev3|dev4|test)\b')
    raw_dt = re.sub(noise, ' ', clean, flags=re.IGNORECASE)
    raw_dt = re.sub(r'https?://\S+', ' ', raw_dt)
    raw_dt = re.sub(r'\s+', ' ', raw_dt).strip()

    if not raw_dt:
        return None

    try:
        scheduled_start, time_specified = parse_datetime_string(raw_dt)
        return {
            "url": url,
            "scheduled_start": scheduled_start,
            "time_specified": time_specified,
        }
    except Exception as e:
        print(f"Could not parse date/time '{raw_dt}': {e}")
        return None


def parse_reschedule_datetime(text: str) -> str | None:
    m = re.search(r'reschedule(?:\s+\w+)?\s+(?:to|for|on)\s+(.+)', text, re.IGNORECASE)
    if not m:
        m = re.search(r'reschedule\s+(.+)', text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).strip()
    raw = re.sub(r'^(it|this|the\s+refresh)?\s*', '', raw, flags=re.IGNORECASE).strip()
    try:
        scheduled_start, _ = parse_datetime_string(raw)
        return scheduled_start
    except Exception as e:
        print(f"Could not parse reschedule datetime: {e}")
        return None


# ---------------------------------------------------------------------------
# Status query handler
# ---------------------------------------------------------------------------

def handle_status_query(text: str, channel_id: str, thread_ts: str):
    """
    Reply with all scheduled/active refresh activities for the requested environment.
    Shows ALL of them (not just the next one).
    """
    env_name, ocid, url = resolve_env_from_text(text)
    activities = list_all_activities(ocid)
    active_states = {"ACCEPTED", "SCHEDULED", "IN_PROGRESS"}
    active = [a for a in activities if a.get("lifecycle-state") in active_states]

    if not active:
        post_thread_reply(channel_id, thread_ts,
            f"No scheduled refreshes found for *{env_name.upper()}*.\nURL: <{url}|{url}>")
        return

    lines = []
    for a in sorted(active, key=lambda x: x.get("time-scheduled-start", "")):
        lifecycle = a.get("lifecycle-state", "")
        scheduled = format_time(a.get("time-scheduled-start", ""))
        short_id  = "..." + a["id"][-12:] if len(a.get("id", "")) > 12 else a.get("id", "")
        lines.append(f"• {scheduled} — `{short_id}` ({lifecycle})")

    count = len(active)
    noun  = "refresh" if count == 1 else "refreshes"
    post_thread_reply(channel_id, thread_ts,
        f"Found {count} scheduled {noun} for *{env_name.upper()}* (<{url}|{url}>):\n"
        + "\n".join(lines))


# ---------------------------------------------------------------------------
# Channel command handler
# ---------------------------------------------------------------------------

def handle_channel_command(msg: dict, bot_mention: str, channel_id: str) -> dict | None:
    """
    Process a new channel message that @mentions the bot.
    Returns new state dict if a refresh was submitted, else None.
    """
    text    = msg.get("text", "")
    msg_ts  = msg.get("ts")

    if bot_mention not in text:
        return None

    text_lower = text.lower()

    # Status query check first
    if is_status_query(text):
        handle_status_query(text, channel_id, msg_ts)
        return None

    # Must contain a refresh-related keyword to be an action
    if not any(kw in text_lower for kw in ("refresh", "schedule", "restart")):
        post_thread_reply(channel_id, msg_ts,
            "I didn't understand that. Supported commands:\n"
            "• `@fbs-admin refresh <url> on July 20 3:00 PM PT` — schedule a refresh\n"
            "• `@fbs-admin when is the next refresh of dev2?` — check scheduled refreshes\n"
            "• `@fbs-admin status dev1` — show all scheduled refreshes for dev1")
        return None

    parsed = parse_channel_command(text)
    if not parsed:
        post_thread_reply(channel_id, msg_ts,
            "I couldn't parse the date/time from your request.\n"
            "Please use a format like:\n"
            "`@fbs-admin refresh <url> on July 20 3:00 PM PT`\n"
            "If you omit the time, I'll default to *5:00 PM PT*.")
        return None

    parsed_url      = parsed["url"]
    scheduled_start = parsed["scheduled_start"]
    time_specified  = parsed["time_specified"]
    formatted_time  = format_time(scheduled_start)

    # Resolve environment
    env_name, target_ocid, target_url = resolve_env_from_text(text)

    time_note = "" if time_specified else "\n_(No time specified — defaulted to *5:00 PM PT*)_"

    # Submit OCI refresh
    result = submit_refresh(scheduled_start, target_ocid=target_ocid)
    if not result:
        post_thread_reply(channel_id, msg_ts,
            "Failed to submit the refresh request. Please check the OCI console.")
        return None

    work_request_id = result.get("opc-work-request-id", "N/A")

    post_thread_reply(channel_id, msg_ts,
        f"Refresh request submitted successfully.{time_note}\n"
        f"*Request ID:* `{work_request_id}`\n"
        f"*Scheduled Date/Time:* {formatted_time}\n"
        f"*URL:* {target_url}\n"
        f"I'll post here when the refresh completes. "
        f"To cancel or reschedule, reply `@fbs-admin cancel` or `@fbs-admin reschedule to <date> <time> PT`.")

    new_state = {
        "channel_id":        channel_id,
        "thread_ts":         msg_ts,
        "scheduled_start":   scheduled_start,
        "work_request_id":   work_request_id,
        "target_ocid":       target_ocid,
        "target_url":        target_url,
        "last_processed_ts": "0",
        "status":            "scheduled",
        "watched_threads":   [],
    }
    return add_watched_thread(new_state, channel_id, msg_ts)


# ---------------------------------------------------------------------------
# Thread command handlers
# ---------------------------------------------------------------------------

def handle_cancel(channel_id: str, thread_ts: str, state: dict) -> dict | None:
    env_ocid   = state.get("target_ocid") or DEV2_OCID
    target_url = state.get("target_url") or DEV2_URL
    activities = list_active_activities(env_ocid)
    if not activities:
        post_thread_reply(channel_id, thread_ts,
            "No active scheduled refresh found in OCI. "
            "It may have already completed or been cancelled.")
        return None

    lines = []
    cancelled = 0
    for a in activities:
        scheduled = format_time(a.get("time-scheduled-start", ""))
        if cancel_activity(a["id"], env_ocid):
            lines.append(f"• `{a['id'][-8:]}...` — scheduled {scheduled} ✓ cancelled")
            cancelled += 1
        else:
            lines.append(f"• `{a['id'][-8:]}...` — scheduled {scheduled} ✗ failed")

    if cancelled:
        detail = "\n".join(lines)
        post_thread_reply(channel_id, thread_ts,
            f"Cancelled {cancelled} refresh activit{'y' if cancelled == 1 else 'ies'}:\n"
            f"{detail}\nURL: {target_url}")
        state["status"] = "cancelled"
        state.pop("activity_id", None)
        return state
    else:
        post_thread_reply(channel_id, thread_ts,
            "Failed to cancel. Please check the OCI console.")
        return None


def handle_reschedule(channel_id: str, thread_ts: str, state: dict, text: str) -> dict | None:
    new_scheduled_start = parse_reschedule_datetime(text)
    if not new_scheduled_start:
        post_thread_reply(channel_id, thread_ts,
            "Could not parse the date/time. Try:\n"
            "• `@fbs-admin reschedule to July 18 4pm PT`\n"
            "• `@fbs-admin reschedule it for July 18 4pm PT`\n"
            "• `@fbs-admin reschedule for July 18` _(defaults to 5:00 PM PT)_")
        return None

    env_ocid   = state.get("target_ocid") or DEV2_OCID
    target_url = state.get("target_url") or DEV2_URL

    for act in list_active_activities(env_ocid):
        cancel_activity(act["id"], env_ocid)

    result = submit_refresh(new_scheduled_start, target_ocid=env_ocid)
    if not result:
        post_thread_reply(channel_id, thread_ts,
            "Failed to submit the rescheduled refresh. Please check the OCI console.")
        return None

    work_request_id = result.get("opc-work-request-id", "N/A")
    formatted_time  = format_time(new_scheduled_start)

    post_thread_reply(channel_id, thread_ts,
        f"Refresh rescheduled successfully.\n"
        f"*Request ID:* `{work_request_id}`\n"
        f"*Scheduled Date/Time:* {formatted_time}\n"
        f"*URL:* {target_url}")

    state["scheduled_start"]  = new_scheduled_start
    state["work_request_id"]  = work_request_id
    state["status"]           = "scheduled"
    state.pop("activity_id", None)
    state.pop("notified_24hr", None)
    return state


# ---------------------------------------------------------------------------
# 24-hour notice (for bot-tracked refreshes)
# ---------------------------------------------------------------------------

def check_24hr_notice(state: dict) -> dict:
    if state.get("notified_24hr"):
        return state

    scheduled_start = state.get("scheduled_start", "")
    if not scheduled_start:
        return state

    try:
        scheduled_dt = datetime.fromisoformat(scheduled_start).astimezone(timezone.utc)
    except Exception:
        return state

    now_utc = datetime.now(timezone.utc)
    delta = scheduled_dt - now_utc

    if timedelta(0) < delta <= timedelta(hours=24):
        channel_id = state["channel_id"]
        thread_ts  = state["thread_ts"]
        wrid       = state.get("work_request_id", "")
        target_url = state.get("target_url") or DEV2_URL
        body = (
            f"URL: <{target_url}|{target_url}>\n"
            f"Scheduled Date/Time: {format_time(scheduled_start)}"
        )
        if wrid:
            body += f"\nRequest ID: `{wrid}`"
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text",
                         "text": "Reminder - Below Oracle Environment is planned to be refreshed in next 24 hours"}
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        ]
        post_message(channel_id, blocks)
        post_thread_reply(channel_id, thread_ts,
            f"24-hour reminder: refresh scheduled for {format_time(scheduled_start)}.\n"
            f"Request ID: `{wrid}`\nURL: {target_url}")
        state["notified_24hr"] = True
        print("24-hour notice posted.")

    return state


# ---------------------------------------------------------------------------
# Completion check (for bot-tracked refreshes)
# ---------------------------------------------------------------------------

def check_completion(state: dict) -> dict:
    scheduled_start = state.get("scheduled_start", "")
    env_ocid        = state.get("target_ocid") or DEV2_OCID

    activity_id = state.get("activity_id")
    if not activity_id:
        activity_id = find_activity_id(scheduled_start, env_ocid)
        if activity_id:
            print(f"Resolved activity_id: {activity_id}")
            state["activity_id"] = activity_id
        else:
            print("Could not resolve activity_id — will retry next run.")
            return state

    activity = get_activity(activity_id, env_ocid)
    if not activity:
        return state

    lifecycle = activity.get("lifecycle-state", "")
    print(f"Activity {activity_id} state: {lifecycle}")

    if lifecycle == "SUCCEEDED":
        time_finished = activity.get("time-finished") or activity.get("time-updated", "")
        target_url = state.get("target_url") or DEV2_URL
        post_completion_notification(
            state["channel_id"], state["thread_ts"],
            time_finished, scheduled_start, activity_id, env_url=target_url
        )
        state["status"]        = "completed"
        state["time_finished"] = time_finished
        print("Completion notification posted.")

    return state


# ---------------------------------------------------------------------------
# Auto-discovery: scan all environments for any scheduled refresh
# (bot-submitted OR UI-submitted) and send 1-week / 24-hour reminders
# ---------------------------------------------------------------------------

def auto_discover_refreshes(state: dict) -> tuple:
    """
    Scan all known environments for active refresh activities.
    Posts 1-week and 24-hour reminders for any activity we haven't already
    notified about, regardless of whether it was submitted via this bot or the OCI UI.

    Returns (updated_state, changed_bool).
    """
    discovered = state.setdefault("discovered_activities", {})
    now_utc    = datetime.now(timezone.utc)
    changed    = False

    # Activity ID already tracked by the main bot-scheduled flow (skip to avoid double-posting)
    main_activity_id = state.get("activity_id")

    for env_name, (ocid, url) in KNOWN_ENVIRONMENTS.items():
        active = list_active_activities(ocid)
        active_ids = {a["id"] for a in active}

        for act in active:
            act_id = act.get("id", "")
            if not act_id or act_id == main_activity_id:
                continue

            scheduled_start = act.get("time-scheduled-start", "")
            try:
                sched_dt = datetime.fromisoformat(scheduled_start).astimezone(timezone.utc)
            except Exception:
                continue

            if act_id not in discovered:
                discovered[act_id] = {
                    "env_name":      env_name,
                    "env_url":       url,
                    "env_ocid":      ocid,
                    "scheduled_start": scheduled_start,
                    "notified_1wk":  False,
                    "notified_24hr": False,
                    "channel_ts":    None,
                    "completed":     False,
                }
                changed = True
                print(f"Auto-discovered new refresh activity: {env_name} / {act_id[-12:]}")

            entry = discovered[act_id]
            delta = sched_dt - now_utc

            # 1-week reminder: 6–8 days before (2-day window catches the daily cron)
            if not entry["notified_1wk"] and timedelta(days=6) <= delta <= timedelta(days=8):
                days_away = delta.days
                body = (
                    f"URL: <{url}|{url}>\n"
                    f"Scheduled Date/Time: {format_time(scheduled_start)}"
                )
                blocks = [
                    {
                        "type": "header",
                        "text": {"type": "plain_text",
                                 "text": f"Reminder - {env_name.upper()} Oracle Environment will be refreshed in ~{days_away} days"}
                    },
                    {"type": "section", "text": {"type": "mrkdwn", "text": body}},
                ]
                _, channel_ts = post_message(SLACK_CHANNEL_ID, blocks)
                entry["notified_1wk"] = True
                entry["channel_ts"]   = channel_ts
                changed = True
                print(f"1-week reminder posted for {env_name} / {act_id[-12:]}")

            # 24-hour reminder: within 24 hours of scheduled start
            if not entry["notified_24hr"] and timedelta(0) < delta <= timedelta(hours=24):
                body = (
                    f"URL: <{url}|{url}>\n"
                    f"Scheduled Date/Time: {format_time(scheduled_start)}"
                )
                blocks = [
                    {
                        "type": "header",
                        "text": {"type": "plain_text",
                                 "text": f"Reminder - {env_name.upper()} Oracle Environment is planned to be refreshed in next 24 hours"}
                    },
                    {"type": "section", "text": {"type": "mrkdwn", "text": body}},
                ]
                _, channel_ts = post_message(SLACK_CHANNEL_ID, blocks)
                entry["notified_24hr"] = True
                if channel_ts and not entry.get("channel_ts"):
                    entry["channel_ts"] = channel_ts
                # Also reply in the 1-week reminder thread if one exists
                if entry.get("channel_ts") and entry["channel_ts"] != channel_ts:
                    post_thread_reply(SLACK_CHANNEL_ID, entry["channel_ts"],
                        f"24-hour reminder: refresh scheduled for {format_time(scheduled_start)}.\n"
                        f"URL: {url}")
                changed = True
                print(f"24-hour reminder posted for {env_name} / {act_id[-12:]}")

        # Check for activities that have left the active list (completed / cancelled)
        for act_id, entry in list(discovered.items()):
            if entry.get("completed"):
                continue
            if entry.get("env_ocid") != ocid:
                continue
            if act_id in active_ids or act_id == main_activity_id:
                continue

            # Activity is gone from active list — check final state
            # Only worth checking if 24hr notice was posted (means it was imminent)
            if not entry.get("notified_24hr"):
                entry["completed"] = True
                changed = True
                continue

            act_data = get_activity(act_id, ocid)
            if not act_data:
                continue

            lifecycle = act_data.get("lifecycle-state", "")
            entry["completed"] = True
            changed = True

            if lifecycle == "SUCCEEDED":
                time_finished = act_data.get("time-finished") or act_data.get("time-updated", "")
                post_completion_notification(
                    SLACK_CHANNEL_ID,
                    entry.get("channel_ts"),     # reply in the reminder thread
                    time_finished,
                    entry["scheduled_start"],
                    act_id,
                    env_url=entry["env_url"],
                )
                print(f"Completion notification posted for {env_name} / {act_id[-12:]}")

    state["discovered_activities"] = discovered
    return state, changed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN not set")
        sys.exit(1)

    state           = load_state()
    bot_user_id     = get_bot_user_id()
    bot_mention     = f"<@{bot_user_id}>"
    changed         = False

    channel_id = state.get("channel_id") or SLACK_CHANNEL_ID

    # ------------------------------------------------------------------
    # 1. Auto-discover refreshes across all environments (bot + UI submitted)
    # ------------------------------------------------------------------
    state, disco_changed = auto_discover_refreshes(state)
    if disco_changed:
        changed = True

    # ------------------------------------------------------------------
    # 2. Scan channel for NEW top-level @bot mentions
    # ------------------------------------------------------------------
    last_channel_ts = state.get("last_channel_ts", "0")
    channel_msgs    = get_channel_messages(channel_id, oldest=last_channel_ts)

    for msg in channel_msgs:
        msg_ts = msg.get("ts", "0")

        new_state = handle_channel_command(msg, bot_mention, channel_id)
        if new_state:
            new_state["last_channel_ts"] = msg_ts
            state   = new_state
            changed = True
            print(f"New refresh submitted from channel command [{msg_ts}].")

        if msg_ts > last_channel_ts:
            last_channel_ts = msg_ts

    state["last_channel_ts"] = last_channel_ts

    # ------------------------------------------------------------------
    # 3. If there is an active bot-tracked refresh, check 24hr notice + completion
    # ------------------------------------------------------------------
    if state.get("status") not in ("cancelled", "completed", ""):
        state   = check_24hr_notice(state)
        state   = check_completion(state)
        changed = True
        if state.get("status") == "completed":
            save_state(state)
            return

    # ------------------------------------------------------------------
    # 4. Process @bot commands across ALL watched threads (current + past)
    # ------------------------------------------------------------------
    if state.get("status") != "completed":
        watched = state.get("watched_threads", [])
        if not watched and state.get("thread_ts"):
            watched = [{"channel_id": channel_id,
                        "thread_ts": state["thread_ts"],
                        "last_processed_ts": state.get("last_processed_ts", "0")}]

        for entry in watched:
            t_channel  = entry.get("channel_id", channel_id)
            t_ts       = entry["thread_ts"]
            t_last     = entry.get("last_processed_ts", "0")

            replies = get_thread_replies(t_channel, t_ts, oldest=t_last)
            for msg in replies:
                msg_ts = msg.get("ts", "0")
                text   = msg.get("text", "")

                if bot_mention not in text:
                    if msg_ts > t_last:
                        t_last = msg_ts
                    continue

                print(f"Processing thread command [{msg_ts}] in thread {t_ts}: {text[:100]}")
                text_lower = text.lower()

                if is_status_query(text):
                    handle_status_query(text, t_channel, t_ts)
                    new_state = None
                elif "reschedule" in text_lower:
                    new_state = handle_reschedule(t_channel, t_ts, state, text)
                elif "cancel" in text_lower:
                    new_state = handle_cancel(t_channel, t_ts, state)
                else:
                    post_thread_reply(t_channel, t_ts,
                        "I didn't understand that. Supported commands:\n"
                        "• `@fbs-admin cancel`\n"
                        "• `@fbs-admin reschedule to <date> <time> PT`\n"
                        "• `@fbs-admin when is the next refresh?` — check scheduled refreshes\n"
                        "_(Omitting the time defaults to 5:00 PM PT)_")
                    new_state = None

                if new_state:
                    state   = new_state
                    changed = True

                if msg_ts > t_last:
                    t_last = msg_ts

            entry["last_processed_ts"] = t_last

        state["watched_threads"] = watched
        state["last_processed_ts"] = watched[0]["last_processed_ts"] if watched else "0"

    if changed or state:
        save_state(state)
        print("State saved.")


if __name__ == "__main__":
    main()
