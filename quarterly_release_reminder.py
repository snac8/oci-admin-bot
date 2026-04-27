#!/usr/bin/env python3
"""
quarterly_release_reminder.py
Runs on the 1st of January, April, July, October.
Creates a Jira ticket in FBSPROJ for the Oracle quarterly release summary
and posts a Slack reminder to #test-ai mentioning the assignee.

Oracle quarter naming: <2-digit-year><A|B|C|D>
  January  → A   (e.g. 26A)
  April    → B   (e.g. 26B)
  July     → C   (e.g. 26C)
  October  → D   (e.g. 26D)

Run modes (--mode or RUN_MODE env var):
  check  : create ticket + post only if today is the 1st of a quarter month (default)
  force  : always create ticket + post (for testing)
"""

import os
import sys
import json
import argparse
from datetime import date

import requests

# --- Config ---
SLACK_BOT_TOKEN  = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL    = os.environ.get("SLACK_CHANNEL", "#test-ai")
JIRA_BASE        = "https://block.atlassian.net"
JIRA_EMAIL       = os.environ.get("JIRA_EMAIL", "sindhun@block.xyz")
JIRA_TOKEN       = os.environ.get("JIRA_TOKEN", "")
JIRA_PROJECT     = "FBSPROJ"
JIRA_ISSUE_TYPE  = "10005"   # Task
JIRA_PRIORITY    = "10005"   # P3
JIRA_COMPONENT   = "20684"   # Controllership
JIRA_PARENT      = "FBSPROJ-2135"  # Oracle Quarterly Releases epic
JINESH_ACCOUNT_ID = "712020:d78910b0-637f-41de-9282-41fd66190934"
JINESH_EMAIL      = "jinesh@block.xyz"

QUARTER_MONTHS = {1: "A", 4: "B", 7: "C", 10: "D"}


def oracle_quarter_label(today: date = None) -> str:
    """Return Oracle quarter label for the current date, e.g. '26B'."""
    d = today or date.today()
    year_short = str(d.year)[2:]
    letter = QUARTER_MONTHS[d.month]
    return f"{year_short}{letter}"


def is_quarter_start(today: date = None) -> bool:
    d = today or date.today()
    return d.day == 1 and d.month in QUARTER_MONTHS


def slack_user_id(email: str) -> str:
    """Return <@UID> mention or email fallback."""
    resp = requests.get(
        "https://slack.com/api/users.lookupByEmail",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params={"email": email},
        timeout=10,
    )
    uid = resp.json().get("user", {}).get("id")
    return f"<@{uid}>" if uid else email


def create_jira_ticket(quarter_label: str) -> str | None:
    summary = f"KLO: {quarter_label} Release Summary"
    description = {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": f"Oracle Fusion quarterly release summary for {quarter_label}. "
                 "Please review the release notes, document key changes, and track any required follow-up actions."}
            ]},
        ],
    }
    body = {
        "fields": {
            "project":           {"key": JIRA_PROJECT},
            "summary":           summary,
            "issuetype":         {"id": JIRA_ISSUE_TYPE},
            "priority":          {"id": JIRA_PRIORITY},
            "components":        [{"id": JIRA_COMPONENT}],
            "assignee":          {"accountId": JINESH_ACCOUNT_ID},
            "customfield_10014": JIRA_PARENT,   # Epic Link
            "parent":            {"key": JIRA_PARENT},
            "description":       description,
        }
    }
    resp = requests.post(
        f"{JIRA_BASE}/rest/api/3/issue",
        auth=(JIRA_EMAIL, JIRA_TOKEN),
        json=body,
        timeout=15,
    )
    data = resp.json()
    key = data.get("key")
    if not key:
        print(f"ERROR creating Jira ticket: {json.dumps(data)[:300]}")
        return None
    print(f"Created Jira ticket: {key} — {summary}")
    return key


def post_slack(quarter_label: str, mention: str, ticket_key: str = None) -> None:
    body = f"{mention} please review and document the *Oracle {quarter_label}* quarterly release."
    if ticket_key:
        body += f"\n\nJira ticket: <{JIRA_BASE}/browse/{ticket_key}|{ticket_key}>"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Reminder - Review Oracle {quarter_label} quarterly release notes"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body}
        },
    ]
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.environ.get("RUN_MODE", "check"),
                        choices=["check", "force"])
    args = parser.parse_args()

    today         = date.today()
    quarter_label = oracle_quarter_label(today)

    print(f"Today        : {today}")
    print(f"Quarter label: {quarter_label}")
    print(f"Mode         : {args.mode}")

    if args.mode == "check" and not is_quarter_start(today):
        print("Not the 1st of a quarter month — nothing to do.")
        return

    mention = slack_user_id(JINESH_EMAIL)

    if args.mode == "force":
        print("Force mode — skipping ticket creation.")
        post_slack(quarter_label, mention)
        return

    ticket_key = create_jira_ticket(quarter_label)
    if not ticket_key:
        sys.exit(1)
    post_slack(quarter_label, mention, ticket_key=ticket_key)


if __name__ == "__main__":
    main()
