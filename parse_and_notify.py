#!/usr/bin/env python3
"""
parse_and_notify.py
Parses the latest Oracle SaaS Usage Metrics Excel report and posts a
formatted summary to a Slack channel via the fbs-admin bot.

Required environment variables:
  SLACK_BOT_TOKEN  - Slack bot token (xoxb-...)
  SLACK_CHANNEL    - Slack channel name (default: #test-ai)
  SAAS_USAGE_DIR   - Directory containing downloaded xlsx files
                     (default: /Users/sindhun/oci/saas-usage)
"""

import os
import sys
import glob
import warnings
import requests
import openpyxl

warnings.filterwarnings("ignore")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#test-ai")
SAAS_USAGE_DIR = os.environ.get("SAAS_USAGE_DIR", "/Users/sindhun/oci/saas-usage")


def find_latest_report():
    files = sorted(glob.glob(os.path.join(SAAS_USAGE_DIR, "SaaS_Service_Usage_Metrics_*.xlsx")))
    if not files:
        print("ERROR: No report files found in", SAAS_USAGE_DIR)
        sys.exit(1)
    return files[-1]


def parse_report(filepath):
    wb = openpyxl.load_workbook(filepath)
    ws = wb["Usage Summary"]

    rows = list(ws.iter_rows(values_only=True))

    # Row index 4 (0-based) = month sub-headers: Jan, Feb, Mar, ...
    month_row = rows[4]
    month1 = str(month_row[3]).strip() if month_row[3] else "M1"
    month2 = str(month_row[4]).strip() if month_row[4] else "M2"
    month3 = str(month_row[5]).strip() if month_row[5] else "M3"

    services = []
    # Data starts at row index 5
    for row in rows[5:]:
        part = row[1]
        service = row[2]
        m1 = row[3]
        m2 = row[4]
        m3 = row[5]
        subscribed = row[6]
        remaining = row[7]
        utilization = row[8]

        # Skip blank or header-like rows
        if not part or not service or not isinstance(m3, (int, float)):
            continue

        # Shorten service name for display
        name = str(service).strip().replace("Oracle Fusion ", "").replace(" Cloud Service - Hosted Named User", "").replace(" Cloud Service - Hosted 1,000 Records", " (1K Records)").replace("\n", "")

        services.append({
            "part": str(part).strip(),
            "name": name,
            "m1": int(m1) if m1 is not None else 0,
            "m2": int(m2) if m2 is not None else 0,
            "m3": int(m3) if m3 is not None else 0,
            "subscribed": int(subscribed) if subscribed is not None else 0,
            "remaining": int(remaining) if remaining is not None else 0,
            "utilization": float(utilization) if utilization is not None else 0.0,
        })

    return month1, month2, month3, services


def format_slack_message(filename, month1, month2, month3, services):
    # Extract report date from filename e.g. _20260301.xlsx -> Mar 2026
    basename = os.path.basename(filename)
    date_str = basename[-13:-5]  # 20260301
    try:
        from datetime import datetime
        report_date = datetime.strptime(date_str, "%Y%m%d").strftime("%B %Y")
    except Exception:
        report_date = date_str

    # Build table
    header = f"{'Service':<42} {month1:>5} {month2:>5} {month3:>5}  {'Sub':>5}  {'Util':>6}"
    divider = "─" * len(header)

    lines = [header, divider]
    alerts = []
    for s in services:
        util_pct = f"{s['utilization']*100:.0f}%"
        flag = " ⚠️" if s["utilization"] >= 0.90 else ""
        line = f"{s['name'][:42]:<42} {s['m1']:>5} {s['m2']:>5} {s['m3']:>5}  {s['subscribed']:>5}  {util_pct:>6}{flag}"
        lines.append(line)
        if s["utilization"] >= 0.90:
            alerts.append(f"• *{s['name']}*: {util_pct} utilized ({s['m3']} of {s['subscribed']})")

    table = "\n".join(lines)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Oracle SaaS Usage Report — {report_date}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_Rolling 3 months: {month1} / {month2} / {month3}_"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```{table}```"}
        },
    ]

    if alerts:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*⚠️ Services at 90%+ utilization:*\n" + "\n".join(alerts)}
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"Source: `{basename}`"}]
    })

    return blocks


def post_to_slack(blocks):
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN environment variable not set.")
        sys.exit(1)

    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=10,
    )
    data = response.json()
    if not data.get("ok"):
        print(f"ERROR: Slack API error: {data.get('error')}")
        sys.exit(1)
    print(f"Posted to {SLACK_CHANNEL} successfully.")


if __name__ == "__main__":
    filepath = find_latest_report()
    print(f"Parsing: {os.path.basename(filepath)}")
    month1, month2, month3, services = parse_report(filepath)
    print(f"Found {len(services)} services. Months: {month1} / {month2} / {month3}")
    blocks = format_slack_message(filepath, month1, month2, month3, services)
    post_to_slack(blocks)
