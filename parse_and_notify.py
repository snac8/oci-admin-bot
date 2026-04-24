#!/usr/bin/env python3
"""
parse_and_notify.py
Parses Oracle SaaS Usage Metrics reports (ERP Excel + EPM PDF), creates Jira
tickets, attaches reports, and posts a combined summary to Slack.

Required environment variables (set in /etc/fbs-admin/secrets.env):
  SLACK_BOT_TOKEN       - Slack bot token (xoxb-...)
  SLACK_CHANNEL         - Slack channel (default: #test-ai)
  SAAS_USAGE_DIR        - Directory with downloaded files
  JIRA_EMAIL            - Atlassian account email
  JIRA_TOKEN            - Jira API token
  JIRA_ASSIGNEE_ERP     - Jira account ID for ERP assignee
  JIRA_ASSIGNEE_EPBCS   - Jira account ID for EPBCS assignee
  JIRA_ASSIGNEE_FCCS_EDM- Jira account ID for FCCS-EDM assignee
  SLACK_USER_ERP        - Slack email for ERP assignee (for @mention)
  SLACK_USER_EPBCS      - Slack email for EPBCS assignee
  SLACK_USER_FCCS_EDM   - Slack email for FCCS-EDM assignee
"""

import os
import re
import sys
import glob
import warnings
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import requests
import openpyxl
import pdfplumber

warnings.filterwarnings("ignore")

# --- Config ---
SLACK_BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL     = os.environ.get("SLACK_CHANNEL", "#test-ai")
SAAS_USAGE_DIR    = os.environ.get("SAAS_USAGE_DIR", "/Users/sindhun/oci/saas-usage")
JIRA_BASE         = "https://block.atlassian.net"
JIRA_EMAIL        = os.environ.get("JIRA_EMAIL", "sindhun@block.xyz")
JIRA_TOKEN        = os.environ.get("JIRA_TOKEN", "")
JIRA_PROJECT      = "FBS"
JIRA_ISSUE_TYPE   = "10005"   # Task
JIRA_PRIORITY     = "10005"   # P3
JIRA_COMP_ERP         = "38102"
JIRA_COMP_EPBCS       = "38103"
JIRA_COMP_FCCS_EDM    = "38104"
JIRA_ASSIGNEE_ERP       = os.environ.get("JIRA_ASSIGNEE_ERP",       "712020:68edc5a8-7a36-4d36-9680-ea796a67a4d2")
JIRA_ASSIGNEE_EPBCS     = os.environ.get("JIRA_ASSIGNEE_EPBCS",     "712020:68edc5a8-7a36-4d36-9680-ea796a67a4d2")
JIRA_ASSIGNEE_FCCS_EDM  = os.environ.get("JIRA_ASSIGNEE_FCCS_EDM",  "712020:68edc5a8-7a36-4d36-9680-ea796a67a4d2")
SLACK_USER_ERP      = os.environ.get("SLACK_USER_ERP",      "sindhun@block.xyz")
SLACK_USER_EPBCS    = os.environ.get("SLACK_USER_EPBCS",    "sindhun@block.xyz")
SLACK_USER_FCCS_EDM = os.environ.get("SLACK_USER_FCCS_EDM", "sindhun@block.xyz")

PREV_MONTH = (date.today().replace(day=1) - relativedelta(months=1)).strftime("%B %Y")


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
        part, service, m1, m2, m3 = row[1], row[2], row[3], row[4], row[5]
        subscribed, remaining, utilization = row[6], row[7], row[8]
        if not part or not service or not isinstance(m3, (int, float)):
            continue
        name = (str(service).strip()
                .replace("Oracle Fusion ", "")
                .replace(" Cloud Service - Hosted Named User", "")
                .replace(" Cloud Service - Hosted 1,000 Records", " (1K Records)")
                .replace("\n", ""))
        services.append({
            "part": str(part).strip(), "name": name,
            "m1": int(m1) if m1 is not None else 0,
            "m2": int(m2) if m2 is not None else 0,
            "m3": int(m3) if m3 is not None else 0,
            "subscribed": int(subscribed) if subscribed is not None else 0,
            "remaining": int(remaining) if remaining is not None else 0,
            "utilization": float(utilization) if utilization is not None else 0.0,
        })
    return month1, month2, month3, services


# ---------------------------------------------------------------------------
# EPM (PDF)
# ---------------------------------------------------------------------------

def find_latest_epm_report():
    files = sorted(glob.glob(os.path.join(SAAS_USAGE_DIR, "SaaS_Service_Usage_Metrics_EPM_*.pdf")))
    return files[-1] if files else None


def _shorten_epm_name(raw):
    lines = [l.strip() for l in raw.split("\n") if l.strip() and not l.strip().startswith("EHSG")]
    name = " - ".join(lines[:2])
    name = re.sub(r"Oracle Enterprise Performance Management\s*", "EPM ", name)
    name = re.sub(r"Oracle Additional Application for Oracle Enterprise\s*", "EPM Additional - ", name)
    name = re.sub(r"Oracle Enterprise Data Management\s*\(EDM\)", "EDM", name)
    name = re.sub(r"Oracle Enterprise Data Management", "EDM", name)
    name = re.sub(r"Performance Management Enterprise Cloud Service\s*-?\s*", "", name)
    name = re.sub(r"\s*Cloud Service\s*-?\s*", " - ", name)
    name = name.replace("Hosted ", "")
    name = re.sub(r"\s*-\s*-\s*", " - ", name)  # collapse double dashes
    return re.sub(r"\s+", " ", name).strip(" -")


def parse_epm_report(filepath):
    """
    Returns:
      months         - (m1, m2, m3) month labels for utilization tables
      services       - list of aggregate utilization services (pages 4, 10, 13)
      detailed_users - list of per-instance {instance, part, gross, unique} (pages 6-7)
      employee       - dict {dec, jan, feb, highest} for Hosted Employee (page 9), or None
    """
    services, months = [], None
    detailed_users = []
    employee = None

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if len(table) < 2:
                    continue
                header = table[0]
                header_str = " ".join(str(h) for h in header if h)

                # --- Aggregate utilization tables (pages 4, 10, 13) ---
                if "Utilization" in header_str and len(table) >= 3:
                    sub = table[1]
                    if months is None:
                        months = (
                            str(sub[2]).strip() if sub[2] else "M1",
                            str(sub[3]).strip() if sub[3] else "M2",
                            str(sub[4]).strip() if sub[4] else "M3",
                        )
                    for row in table[2:]:
                        part, service = row[0], row[1]
                        if not part or not service:
                            continue
                        try:
                            m1 = float(str(row[2]).replace(",", "")) if row[2] else 0
                            m2 = float(str(row[3]).replace(",", "")) if row[3] else 0
                            m3 = float(str(row[4]).replace(",", "")) if row[4] else 0
                            subscribed = float(str(row[5]).replace(",", "")) if row[5] else 0
                            remaining = float(str(row[6]).replace(",", "")) if row[6] else 0
                            utilization = float(str(row[7]).replace("%", "").strip()) / 100 if row[7] else 0
                        except (ValueError, TypeError, IndexError):
                            continue
                        services.append({
                            "part": str(part).strip(),
                            "name": _shorten_epm_name(str(service)),
                            "m1": m1, "m2": m2, "m3": m3,
                            "subscribed": subscribed, "remaining": remaining,
                            "utilization": utilization,
                        })

                # --- Detailed per-instance user table (pages 6-7) ---
                elif "Gross" in header_str and "Unique" in header_str:
                    for row in table[1:]:
                        part, service = row[0], row[1]
                        if not part or not service or str(part).startswith("Total"):
                            continue
                        # Extract instance ID: last EHSG.* line in service cell
                        lines = [l.strip() for l in str(service).split("\n") if l.strip()]
                        instance = next((l for l in reversed(lines) if l.startswith("EHSG")), lines[-1])
                        try:
                            gross = int(str(row[2]).replace(",", "")) if row[2] else 0
                            unique = int(str(row[3]).replace(",", "")) if row[3] else 0
                        except (ValueError, TypeError):
                            continue
                        detailed_users.append({
                            "part": str(part).strip(),
                            "instance": instance,
                            "gross": gross,
                            "unique": unique,
                        })

                # --- Hosted Employee table (page 9) ---
                elif "Hosted Employee Quantity" in header_str and len(table) >= 3:
                    sub = table[1]
                    for row in table[2:]:
                        if not row[0] or not row[1]:
                            continue
                        try:
                            employee = {
                                "dec": int(str(row[2]).replace(",", "")) if row[2] else 0,
                                "jan": int(str(row[3]).replace(",", "")) if row[3] else 0,
                                "feb": int(str(row[4]).replace(",", "")) if row[4] else 0,
                                "highest": str(row[5]).strip() if row[5] else "-",
                                "m1": str(sub[2]).strip() if sub[2] else "Dec",
                                "m2": str(sub[3]).strip() if sub[3] else "Jan",
                                "m3": str(sub[4]).strip() if sub[4] else "Feb",
                            }
                        except (ValueError, TypeError, IndexError):
                            continue

    return months, services, detailed_users, employee


def _split_epm(services, detailed_users):
    """Split EPM services by part number: B91074 (Hosted Named User) → EPBCS; rest → FCCS-EDM."""
    epbcs = [s for s in services if s["part"] == "B91074"]
    fccs_edm = [s for s in services if s["part"] != "B91074"]
    # PLAN instances → EPBCS detail; EDM/FCCS/other instances → FCCS-EDM detail
    epbcs_detail = [d for d in detailed_users if "PLAN" in d["instance"]]
    fccs_detail   = [d for d in detailed_users if "PLAN" not in d["instance"]]
    return epbcs, fccs_edm, epbcs_detail, fccs_detail


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

def _jira_auth():
    return (JIRA_EMAIL, JIRA_TOKEN)


def _build_jira_desc(intro, months, services, fmt_val=None):
    """Build ADF description with a usage table."""
    header = f"{'Service':<42} {months[0]:>5} {months[1]:>5} {months[2]:>5}  {'Sub':>6}  {'Util':>6}"
    divider = "─" * len(header)
    lines = [header, divider]
    for s in services:
        util_pct = f"{s['utilization']*100:.0f}%"
        flag = " ⚠" if s["utilization"] >= 0.90 else ""
        v1 = fmt_val(s["m1"]) if fmt_val else str(s["m1"])
        v2 = fmt_val(s["m2"]) if fmt_val else str(s["m2"])
        v3 = fmt_val(s["m3"]) if fmt_val else str(s["m3"])
        sub = fmt_val(s["subscribed"]) if fmt_val else str(s["subscribed"])
        lines.append(f"{s['name'][:42]:<42} {v1:>5} {v2:>5} {v3:>5}  {sub:>6}  {util_pct:>6}{flag}")
    table_text = "\n".join(lines)
    return {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": intro}]},
            {"type": "codeBlock", "attrs": {"language": ""},
             "content": [{"type": "text", "text": table_text}]},
        ]
    }


def create_jira_ticket(summary, component_id, assignee_id, description):
    resp = requests.post(
        f"{JIRA_BASE}/rest/api/3/issue",
        auth=_jira_auth(),
        json={"fields": {
            "project": {"key": JIRA_PROJECT},
            "summary": summary,
            "issuetype": {"id": JIRA_ISSUE_TYPE},
            "priority": {"id": JIRA_PRIORITY},
            "components": [{"id": component_id}],
            "assignee": {"accountId": assignee_id},
            "description": description,
        }},
        timeout=15,
    )
    data = resp.json()
    key = data.get("key")
    if not key:
        print(f"ERROR creating Jira ticket: {data}")
        return None
    print(f"Created Jira ticket: {key}")
    return key


def attach_to_jira(ticket_key, filepath):
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{JIRA_BASE}/rest/api/3/issue/{ticket_key}/attachments",
            auth=_jira_auth(),
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (os.path.basename(filepath), f)},
            timeout=60,
        )
    data = resp.json()
    if isinstance(data, list) and data:
        print(f"Attached {data[0]['filename']} to {ticket_key}")
    else:
        print(f"ERROR attaching to {ticket_key}: {data}")


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def _slack_user_id(email):
    """Look up Slack user ID by email. Returns '<@ID>' or display name fallback."""
    try:
        resp = requests.get(
            "https://slack.com/api/users.lookupByEmail",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            params={"email": email},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            return f"<@{data['user']['id']}>"
    except Exception:
        pass
    return email.split("@")[0]  # fallback to username


def _usage_table(months, services, int_vals=True):
    m1, m2, m3 = months
    header = f"{'Service':<42} {m1:>5} {m2:>5} {m3:>5}  {'Sub':>6}  {'Util':>6}"
    divider = "─" * len(header)
    lines = [header, divider]
    alerts = []
    for s in services:
        util_pct = f"{s['utilization']*100:.0f}%"
        flag = " ⚠️" if s["utilization"] >= 0.90 else ""
        if int_vals:
            v1, v2, v3 = str(s["m1"]), str(s["m2"]), str(s["m3"])
            sub = str(s["subscribed"])
        else:
            v1 = f"{s['m1']:.3f}".rstrip("0").rstrip(".")
            v2 = f"{s['m2']:.3f}".rstrip("0").rstrip(".")
            v3 = f"{s['m3']:.3f}".rstrip("0").rstrip(".")
            sub = f"{s['subscribed']:.0f}"
        lines.append(f"{s['name'][:42]:<42} {v1:>5} {v2:>5} {v3:>5}  {sub:>6}  {util_pct:>6}{flag}")
        if s["utilization"] >= 0.90:
            alerts.append(f"• *{s['name']}*: {util_pct} utilized")
    return "\n".join(lines), alerts


def _detail_table(detail_rows, latest_month="Feb"):
    """Format per-instance Gross/Unique user table."""
    header = f"{'Instance':<20} {'Gross':>6} {'Unique':>7}"
    divider = "─" * len(header)
    lines = [header, divider]
    for d in detail_rows:
        lines.append(f"{d['instance']:<20} {d['gross']:>6} {d['unique']:>7}")
    return "\n".join(lines)


def _employee_table(emp, latest_month="Feb"):
    """Format Hosted Employee counts table."""
    header = f"{'Metric':<30} {emp['m1']:>6} {emp['m2']:>6} {emp['m3']:>6}  {'Highest':>8}"
    divider = "─" * len(header)
    line = f"{'Hosted Employees (EDM)':<30} {emp['dec']:>6,} {emp['jan']:>6,} {emp['feb']:>6,}  {emp['highest']:>8}"
    return "\n".join([header, divider, line])


def post_combined_slack(
    erp_file, erp_months, erp_services,
    epm_file, epm_months, epbcs_services, fccs_edm_services,
    epbcs_detail, fccs_detail, employee,
    erp_key, epbcs_key, fccs_edm_key,
):
    mention_erp      = _slack_user_id(SLACK_USER_ERP)
    mention_epbcs    = _slack_user_id(SLACK_USER_EPBCS)
    mention_fccs_edm = _slack_user_id(SLACK_USER_FCCS_EDM)

    jira_url = JIRA_BASE + "/browse"
    ticket_line = (
        f"<{jira_url}/{erp_key}|{erp_key}> ERP → {mention_erp}    "
        f"<{jira_url}/{epbcs_key}|{epbcs_key}> EPBCS → {mention_epbcs}    "
        f"<{jira_url}/{fccs_edm_key}|{fccs_edm_key}> FCCS-EDM → {mention_fccs_edm}"
    )

    erp_table, erp_alerts = _usage_table(
        (erp_months[0], erp_months[1], erp_months[2]), erp_services, int_vals=True
    )
    epbcs_table, epbcs_alerts = _usage_table(epm_months, epbcs_services, int_vals=False)
    fccs_table, fccs_alerts   = _usage_table(epm_months, fccs_edm_services, int_vals=False)
    all_alerts = erp_alerts + epbcs_alerts + fccs_alerts

    m3_label = epm_months[2] if epm_months else "Feb"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Reminder - Monthly Oracle SaaS License Usage - {PREV_MONTH}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{ticket_line}\n\n*Tickets and assignees please take action.*"}
        },
        {"type": "divider"},
        # ERP
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*ERP* — {erp_months[0]} / {erp_months[1]} / {erp_months[2]}\n```{erp_table}```"}
        },
        {"type": "divider"},
        # EPBCS aggregate
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*EPM / EPBCS* — {epm_months[0]} / {epm_months[1]} / {epm_months[2]}\n```{epbcs_table}```"}
        },
    ]

    # EPBCS per-instance detail
    if epbcs_detail:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*EPBCS — Detailed Usage by Environment ({m3_label})*\n```{_detail_table(epbcs_detail, m3_label)}```"}
        })

    blocks.append({"type": "divider"})

    # FCCS-EDM aggregate
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*EPM / FCCS-EDM* — {epm_months[0]} / {epm_months[1]} / {epm_months[2]}\n```{fccs_table}```"}
    })

    # Hosted Employee
    if employee:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Hosted Employee (EDM)* — {employee['m1']} / {employee['m2']} / {employee['m3']}\n```{_employee_table(employee, m3_label)}```"}
        })

    # FCCS-EDM per-instance detail
    if fccs_detail:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*FCCS-EDM — Detailed Usage by Environment ({m3_label})*\n```{_detail_table(fccs_detail, m3_label)}```"}
        })

    if all_alerts:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*⚠️ Services at 90%+ utilization:*\n" + "\n".join(all_alerts)}
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": (
            f"ERP: `{os.path.basename(erp_file)}`  |  EPM: `{os.path.basename(epm_file)}`"
        )}]
    })

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"ERROR: Slack API error: {data.get('error')}")
        sys.exit(1)
    print(f"Posted combined summary to {SLACK_CHANNEL}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # --- Parse ERP ---
    erp_file = find_latest_erp_report()
    print(f"Parsing ERP: {os.path.basename(erp_file)}")
    erp_m1, erp_m2, erp_m3, erp_services = parse_erp_report(erp_file)
    print(f"  {len(erp_services)} services. Months: {erp_m1} / {erp_m2} / {erp_m3}")

    # --- Parse EPM ---
    epm_file = find_latest_epm_report()
    if not epm_file:
        print("ERROR: No EPM report found.")
        sys.exit(1)
    print(f"Parsing EPM: {os.path.basename(epm_file)}")
    epm_months, epm_services, detailed_users, employee = parse_epm_report(epm_file)
    epbcs_services, fccs_edm_services, epbcs_detail, fccs_detail = _split_epm(epm_services, detailed_users)
    print(f"  {len(epbcs_services)} EPBCS, {len(fccs_edm_services)} FCCS-EDM services | {len(detailed_users)} detailed instances | employee={employee is not None}")

    # --- Create Jira tickets ---
    erp_key = create_jira_ticket(
        f"Monthly usage tracking ERP — {PREV_MONTH}",
        JIRA_COMP_ERP, JIRA_ASSIGNEE_ERP,
        _build_jira_desc(
            f"Monthly Oracle ERP SaaS license usage review for {PREV_MONTH}. See attached Excel report.",
            (erp_m1, erp_m2, erp_m3), erp_services,
        ),
    )
    epbcs_key = create_jira_ticket(
        f"Monthly usage tracking EPBCS — {PREV_MONTH}",
        JIRA_COMP_EPBCS, JIRA_ASSIGNEE_EPBCS,
        _build_jira_desc(
            f"Monthly Oracle EPM/EPBCS SaaS license usage review for {PREV_MONTH}. See attached PDF report.",
            epm_months, epbcs_services,
        ),
    )
    fccs_edm_key = create_jira_ticket(
        f"Monthly usage tracking FCCS-EDM — {PREV_MONTH}",
        JIRA_COMP_FCCS_EDM, JIRA_ASSIGNEE_FCCS_EDM,
        _build_jira_desc(
            f"Monthly Oracle EPM/FCCS+EDM SaaS license usage review for {PREV_MONTH}. See attached PDF report.",
            epm_months, fccs_edm_services,
        ),
    )

    # --- Attach files ---
    if erp_key:
        attach_to_jira(erp_key, erp_file)
    if epbcs_key:
        attach_to_jira(epbcs_key, epm_file)
    if fccs_edm_key:
        attach_to_jira(fccs_edm_key, epm_file)

    # --- Post to Slack ---
    post_combined_slack(
        erp_file, (erp_m1, erp_m2, erp_m3), erp_services,
        epm_file, epm_months, epbcs_services, fccs_edm_services,
        epbcs_detail, fccs_detail, employee,
        erp_key or "FBS-???", epbcs_key or "FBS-???", fccs_edm_key or "FBS-???",
    )
