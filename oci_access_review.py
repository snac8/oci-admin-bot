#!/usr/bin/env python3
"""
oci_access_review.py
Quarterly OCI user access review reminder.
Runs on the 1st of January, April, July, October.

1. Exports OCI IAM user list to CSV
2. Ensures the FBS epic "OCI User Access Review and Cleanup" exists (creates once, reuses forever)
3. Creates a Jira task under that epic, assigned to rkoduri@block.xyz
4. Attaches the CSV to the Jira ticket
5. Posts to Slack #test-ai with @mention and CSV file attachment

Run modes (--mode or RUN_MODE env var):
  check  : run only if today is the 1st of a quarter month (default)
  force  : always run (for testing)
"""

import os
import sys
import csv
import json
import argparse
import subprocess
import tempfile
from datetime import date, datetime, timezone

import requests

# --- Config ---
SLACK_BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL     = os.environ.get("SLACK_CHANNEL", "#test-ai")
SLACK_CHANNEL_ID  = os.environ.get("SLACK_CHANNEL_ID", "C0ALU5462EB")
JIRA_BASE         = "https://block.atlassian.net"
JIRA_EMAIL        = os.environ.get("JIRA_EMAIL", "sindhun@block.xyz")
JIRA_TOKEN        = os.environ.get("JIRA_TOKEN", "")
JIRA_PROJECT      = "FBS"
JIRA_ISSUE_TYPE   = "10005"   # Task
JIRA_EPIC_TYPE    = "10000"   # Epic
JIRA_PRIORITY     = "10004"   # P2 (Important)
JIRA_COMPONENT    = "11221"   # Access - Oracle
JIRA_SPRINT       = 1919      # FBS Operations Support
JIRA_EPIC_SUMMARY = "OCI User Access Review and Cleanup"
RAMESH_ACCOUNT_ID = "63572d1d548f1fe6f0c5b44a"   # rkoduri@block.xyz
RAMESH_EMAIL      = "rkoduri@block.xyz"

IDCS_ENDPOINT  = "https://idcs-bd160fc37be34d88a783a331e2f091c7.identity.oraclecloud.com"
QUARTER_MONTHS = {1: "Q1", 4: "Q2", 7: "Q3", 10: "Q4"}
OCI_NAMESPACE  = "axbix6knqxie"
OCI_BUCKET     = "fbs-admin-state"
EPIC_STATE_OBJ = "oci_access_review_epic.json"
EPIC_STATE_FILE = "/tmp/oci_access_review_epic.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_quarter_start(today: date = None) -> bool:
    d = today or date.today()
    return d.day == 1 and d.month in QUARTER_MONTHS


def quarter_label(today: date = None) -> str:
    d = today or date.today()
    return f"{QUARTER_MONTHS[d.month]} {d.year}"


def _jira_auth():
    return (JIRA_EMAIL, JIRA_TOKEN)


def slack_mention(email: str) -> str:
    resp = requests.get(
        "https://slack.com/api/users.lookupByEmail",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params={"email": email},
        timeout=10,
    )
    uid = resp.json().get("user", {}).get("id")
    return f"<@{uid}>" if uid else email


# ---------------------------------------------------------------------------
# OCI user export
# ---------------------------------------------------------------------------

def export_oci_users(csv_path: str) -> int:
    """Export all IDCS users to CSV. Returns number of users written."""
    result = subprocess.run(
        ["oci", "--endpoint", IDCS_ENDPOINT,
         "identity-domains", "users", "list", "--all"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR fetching IDCS users: {result.stderr.strip()}")
        sys.exit(1)

    users = json.loads(result.stdout).get("data", {}).get("resources", [])

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Username", "Display Name", "Email", "Status",
            "User Type", "Created (UTC)", "Last Modified (UTC)",
        ])
        for u in sorted(users, key=lambda x: x.get("user-name", "")):
            username    = u.get("user-name", "")
            display     = u.get("display-name", "")
            emails      = u.get("emails") or []
            email       = next((e["value"] for e in emails if e.get("primary")), username)
            status      = "Active" if u.get("active") else "Inactive"
            user_type   = u.get("user-type") or "N/A"
            meta        = u.get("meta") or {}
            created     = (meta.get("created") or "")[:19].replace("T", " ")
            last_mod    = (meta.get("last-modified") or "")[:19].replace("T", " ")
            writer.writerow([username, display, email, status, user_type, created, last_mod])

    active = sum(1 for u in users if u.get("active"))
    print(f"Exported {len(users)} IDCS users ({active} active) to {csv_path}")
    return len(users)


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

def load_epic_key() -> str | None:
    """Load the stored epic key from OCI Object Storage."""
    result = subprocess.run([
        "oci", "os", "object", "get",
        "--namespace", OCI_NAMESPACE,
        "--bucket-name", OCI_BUCKET,
        "--name", EPIC_STATE_OBJ,
        "--file", EPIC_STATE_FILE,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        with open(EPIC_STATE_FILE) as f:
            return json.load(f).get("epic_key")
    except Exception:
        return None


def save_epic_key(epic_key: str):
    """Persist the epic key to OCI Object Storage."""
    with open(EPIC_STATE_FILE, "w") as f:
        json.dump({"epic_key": epic_key, "summary": JIRA_EPIC_SUMMARY}, f)
    result = subprocess.run([
        "oci", "os", "object", "put",
        "--namespace", OCI_NAMESPACE,
        "--bucket-name", OCI_BUCKET,
        "--name", EPIC_STATE_OBJ,
        "--file", EPIC_STATE_FILE,
        "--force",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: could not save epic key to OCI: {result.stderr.strip()}")


def get_or_create_epic() -> str:
    """Return the epic key, creating it only once and persisting for future runs."""
    existing = load_epic_key()
    if existing:
        print(f"Reusing existing epic: {existing}")
        return existing

    body = {
        "fields": {
            "project":    {"key": JIRA_PROJECT},
            "summary":    JIRA_EPIC_SUMMARY,
            "issuetype":  {"id": JIRA_EPIC_TYPE},
            "components": [{"id": JIRA_COMPONENT}],
            "priority":   {"id": JIRA_PRIORITY},
        }
    }
    resp = requests.post(
        f"{JIRA_BASE}/rest/api/3/issue",
        auth=_jira_auth(),
        json=body,
        timeout=15,
    )
    data = resp.json()
    key = data.get("key")
    if not key:
        print(f"ERROR creating epic: {data}")
        sys.exit(1)
    print(f"Created new epic: {key} — {JIRA_EPIC_SUMMARY}")
    save_epic_key(key)
    return key


def create_ticket(epic_key: str, label: str, user_count: int) -> str | None:
    today = date.today()
    summary = f"OCI User Access Cleanup - {today.strftime('%B %Y')}"
    body = {
        "fields": {
            "project":           {"key": JIRA_PROJECT},
            "summary":           summary,
            "issuetype":         {"id": JIRA_ISSUE_TYPE},
            "priority":          {"id": JIRA_PRIORITY},
            "components":        [{"id": JIRA_COMPONENT}],
            "assignee":          {"accountId": RAMESH_ACCOUNT_ID},
            "customfield_10014": epic_key,
            "customfield_10020": JIRA_SPRINT,
        }
    }
    resp = requests.post(
        f"{JIRA_BASE}/rest/api/3/issue",
        auth=_jira_auth(),
        json=body,
        timeout=15,
    )
    data = resp.json()
    key = data.get("key")
    if not key:
        print(f"ERROR creating ticket: {data}")
        return None
    print(f"Created ticket: {key} — {summary}")
    return key


def attach_to_jira(ticket_key: str, filepath: str):
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{JIRA_BASE}/rest/api/3/issue/{ticket_key}/attachments",
            auth=_jira_auth(),
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (os.path.basename(filepath), f, "text/csv")},
            timeout=30,
        )
    data = resp.json()
    if isinstance(data, list) and data:
        print(f"Attached {data[0]['filename']} to {ticket_key}")
    else:
        print(f"ERROR attaching to {ticket_key}: {data}")


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def upload_slack_file(csv_path: str, label: str) -> str | None:
    """Upload CSV to Slack and return the file permalink."""
    filename = os.path.basename(csv_path)
    file_size = os.path.getsize(csv_path)

    # Step 1: get upload URL
    resp = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        data={"filename": filename, "length": file_size},
        timeout=10,
    )
    d = resp.json()
    if not d.get("ok"):
        print(f"ERROR getting upload URL: {d.get('error')}")
        return None
    upload_url = d["upload_url"]
    file_id    = d["file_id"]

    # Step 2: upload content
    with open(csv_path, "rb") as f:
        requests.post(upload_url, data=f, timeout=30)

    # Step 3: complete upload and share to channel
    resp = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                 "Content-Type": "application/json"},
        json={
            "files": [{"id": file_id, "title": f"OCI Users — {label}"}],
            "channel_id": SLACK_CHANNEL_ID,
        },
        timeout=10,
    )
    d = resp.json()
    if not d.get("ok"):
        print(f"ERROR completing upload: {d.get('error')}")
        return None
    permalink = d.get("files", [{}])[0].get("permalink")
    print(f"Uploaded {filename} to Slack (file_id={file_id})")
    return permalink


def post_slack(label: str, ticket_key: str, user_count: int, mention: str, file_permalink: str = None):
    ticket_url = f"{JIRA_BASE}/browse/{ticket_key}"
    body = (
        f"{mention} please review and action the OCI user access list for *{label}*.\n\n"
        f"*Users exported:* {user_count}\n"
        f"*Jira ticket:* <{ticket_url}|{ticket_key}>\n"
    )
    if file_permalink:
        body += f"*User list:* <{file_permalink}|Download CSV>"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Reminder - OCI User Access Review {label}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body}
        },
    ]
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                 "Content-Type": "application/json"},
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"ERROR: Slack post failed: {data.get('error')}")
        sys.exit(1)
    print(f"Posted to {SLACK_CHANNEL} (ts={data.get('ts')})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.environ.get("RUN_MODE", "check"),
                        choices=["check", "force"])
    args = parser.parse_args()

    today = date.today()
    label = quarter_label(today)
    print(f"Today  : {today}")
    print(f"Quarter: {label}")
    print(f"Mode   : {args.mode}")

    if args.mode == "check" and not is_quarter_start(today):
        print("Not the 1st of a quarter month — nothing to do.")
        return

    # 1. Export OCI users
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False,
                                     prefix=f"oci_users_{today.strftime('%Y%m%d')}_") as tmp:
        csv_path = tmp.name
    user_count = export_oci_users(csv_path)

    # 2. Ensure epic exists (create once, reuse forever)
    epic_key = get_or_create_epic()

    # 3. Create Jira ticket
    ticket_key = create_ticket(epic_key, label, user_count)
    if not ticket_key:
        sys.exit(1)

    # 4. Attach CSV to Jira ticket
    attach_to_jira(ticket_key, csv_path)

    # 5. Upload CSV to Slack and post
    mention       = slack_mention(RAMESH_EMAIL)
    file_permalink = upload_slack_file(csv_path, label)
    post_slack(label, ticket_key, user_count, mention, file_permalink)

    os.unlink(csv_path)


if __name__ == "__main__":
    main()
