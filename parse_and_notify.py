#!/usr/bin/env python3
"""
parse_and_notify.py
Parses Oracle SaaS Usage Metrics reports (ERP Excel + EPM PDF) and posts
formatted summaries to a Slack channel via the fbs-admin bot.

Required environment variables:
  SLACK_BOT_TOKEN  - Slack bot token (xoxb-...)
  SLACK_CHANNEL    - Slack channel name (default: #test-ai)
  SAAS_USAGE_DIR   - Directory containing downloaded files
                     (default: /Users/sindhun/oci/saas-usage)
"""

import os
import re
import sys
import glob
import warnings
import requests
import openpyxl
import pdfplumber

warnings.filterwarnings("ignore")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#test-ai")
SAAS_USAGE_DIR = os.environ.get("SAAS_USAGE_DIR", "/Users/sindhun/oci/saas-usage")


# ---------------------------------------------------------------------------
# ERP (Excel)
# ---------------------------------------------------------------------------

def find_latest_erp_report():
    files = sorted(glob.glob(os.path.join(SAAS_USAGE_DIR, "SaaS_Service_Usage_Metrics_Drill_Through_*.xlsx")))
    if not files:
        print("ERROR: No ERP report files found in", SAAS_USAGE_DIR)
        sys.exit(1)
    return files[-1]


def parse_erp_report(filepath):
    wb = openpyxl.load_workbook(filepath)
    ws = wb["Usage Summary"]

    rows = list(ws.iter_rows(values_only=True))

    month_row = rows[4]
    month1 = str(month_row[3]).strip() if month_row[3] else "M1"
    month2 = str(month_row[4]).strip() if month_row[4] else "M2"
    month3 = str(month_row[5]).strip() if month_row[5] else "M3"

    services = []
    for row in rows[5:]:
        part = row[1]
        service = row[2]
        m1 = row[3]
        m2 = row[4]
        m3 = row[5]
        subscribed = row[6]
        remaining = row[7]
        utilization = row[8]

        if not part or not service or not isinstance(m3, (int, float)):
            continue

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


def format_erp_slack_blocks(filename, month1, month2, month3, services):
    basename = os.path.basename(filename)
    date_str = basename[-13:-5]
    try:
        from datetime import datetime
        report_date = datetime.strptime(date_str, "%Y%m%d").strftime("%B %Y")
    except Exception:
        report_date = date_str

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
            "text": {"type": "plain_text", "text": f"Oracle ERP Usage Report — {report_date}"}
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


# ---------------------------------------------------------------------------
# EPM (PDF)
# ---------------------------------------------------------------------------

def find_latest_epm_report():
    files = sorted(glob.glob(os.path.join(SAAS_USAGE_DIR, "SaaS_Service_Usage_Metrics_EPM_*.pdf")))
    if not files:
        return None
    return files[-1]


def _shorten_epm_name(raw):
    # Take first non-empty line (avoids instance IDs like EHSG.PLAN1)
    first_line = raw.split("\n")[0].strip()
    first_line = first_line.replace("Oracle Enterprise Performance Management ", "EPM ")
    first_line = first_line.replace("Oracle Additional Application for Oracle Enterprise", "EPM Additional")
    first_line = first_line.replace("Oracle Enterprise Data Management", "EDM")
    first_line = first_line.replace("Performance Management Enterprise Cloud Service -", "")
    first_line = first_line.replace(" Cloud Service", "")
    first_line = re.sub(r"\s+", " ", first_line).strip()
    return first_line


def parse_epm_report(filepath):
    services = []
    months = None

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if len(table) < 3:
                    continue
                header = table[0]
                sub_header = table[1]

                # Only process tables with subscription utilization data
                if not any("Utilization" in str(h) for h in header if h):
                    continue

                if months is None:
                    months = (
                        str(sub_header[2]).strip() if sub_header[2] else "M1",
                        str(sub_header[3]).strip() if sub_header[3] else "M2",
                        str(sub_header[4]).strip() if sub_header[4] else "M3",
                    )

                for row in table[2:]:
                    part = row[0]
                    service = row[1]
                    if not part or not service:
                        continue

                    try:
                        m1 = float(str(row[2]).replace(",", "")) if row[2] else 0
                        m2 = float(str(row[3]).replace(",", "")) if row[3] else 0
                        m3 = float(str(row[4]).replace(",", "")) if row[4] else 0
                        subscribed = float(str(row[5]).replace(",", "")) if row[5] else 0
                        remaining = float(str(row[6]).replace(",", "")) if row[6] else 0
                        util_str = str(row[7]).replace("%", "").strip() if row[7] else "0"
                        utilization = float(util_str) / 100
                    except (ValueError, TypeError, IndexError):
                        continue

                    services.append({
                        "part": str(part).strip(),
                        "name": _shorten_epm_name(str(service)),
                        "m1": m1,
                        "m2": m2,
                        "m3": m3,
                        "subscribed": subscribed,
                        "remaining": remaining,
                        "utilization": utilization,
                    })

    return months, services


def format_epm_slack_blocks(filename, months, services):
    basename = os.path.basename(filename)
    date_str = basename[-12:-4]  # 20260327
    try:
        from datetime import datetime
        report_date = datetime.strptime(date_str, "%Y%m%d").strftime("%B %Y")
    except Exception:
        report_date = date_str

    month1, month2, month3 = months if months else ("M1", "M2", "M3")

    header = f"{'Service':<42} {month1:>5} {month2:>5} {month3:>5}  {'Sub':>6}  {'Util':>6}"
    divider = "─" * len(header)

    lines = [header, divider]
    alerts = []
    for s in services:
        util_pct = f"{s['utilization']*100:.0f}%"
        flag = " ⚠️" if s["utilization"] >= 0.90 else ""
        m1_str = f"{s['m1']:.0f}" if isinstance(s['m1'], float) and s['m1'] == int(s['m1']) else f"{s['m1']}"
        m2_str = f"{s['m2']:.0f}" if isinstance(s['m2'], float) and s['m2'] == int(s['m2']) else f"{s['m2']}"
        m3_str = f"{s['m3']:.0f}" if isinstance(s['m3'], float) and s['m3'] == int(s['m3']) else f"{s['m3']}"
        sub_str = f"{s['subscribed']:.0f}" if isinstance(s['subscribed'], float) and s['subscribed'] == int(s['subscribed']) else f"{s['subscribed']}"
        line = f"{s['name'][:42]:<42} {m1_str:>5} {m2_str:>5} {m3_str:>5}  {sub_str:>6}  {util_pct:>6}{flag}"
        lines.append(line)
        if s["utilization"] >= 0.90:
            alerts.append(f"• *{s['name']}*: {util_pct} utilized ({s['m3']} of {s['subscribed']})")

    table = "\n".join(lines)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Oracle EPM Usage Report — {report_date}"}
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


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ERP
    erp_file = find_latest_erp_report()
    print(f"Parsing ERP: {os.path.basename(erp_file)}")
    month1, month2, month3, erp_services = parse_erp_report(erp_file)
    print(f"Found {len(erp_services)} ERP services. Months: {month1} / {month2} / {month3}")
    post_to_slack(format_erp_slack_blocks(erp_file, month1, month2, month3, erp_services))

    # EPM
    epm_file = find_latest_epm_report()
    if epm_file:
        print(f"Parsing EPM: {os.path.basename(epm_file)}")
        months, epm_services = parse_epm_report(epm_file)
        print(f"Found {len(epm_services)} EPM services. Months: {months[0]} / {months[1]} / {months[2]}")
        post_to_slack(format_epm_slack_blocks(epm_file, months, epm_services))
    else:
        print("No EPM report found, skipping.")
