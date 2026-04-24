# FBS Admin Bot — Agent Configuration

This file configures the behavior of the fbs-admin Slack bot. Edit this file in plain English to change assignments, channels, schedules, and reminders. The bot reads this file on each run.

---

## 1. Jira Ticket Assignments

One Jira ticket is created per domain every month in project **FBS**. Assign each domain to the person responsible for reviewing and actioning the usage data.

| Domain | Component | Assignee Name | Assignee Email |
|--------|-----------|---------------|----------------|
| ERP | usagetracking-ERP | Sindhu Nachimuthu | sindhun@block.xyz |
| EPBCS | usagetracking-EPBCS | Sindhu Nachimuthu | sindhun@block.xyz |
| FCCS-EDM | usagetracking-FCCS-EDM | Sindhu Nachimuthu | sindhun@block.xyz |

> **Note:** When ready to assign to real owners, update the names and emails above.
> The bot will look up Jira account IDs and Slack user IDs automatically from the email addresses.
> Intended production assignees:
> - ERP → Ramesh Koduri (ramesh@block.xyz)
> - EPBCS → Sahle Melaku (sahle@block.xyz)
> - FCCS-EDM → Abbas Ali Mogal (abbas@block.xyz)

---

## 2. Jira Ticket Settings

- **Project**: FBS
- **Issue Type**: Task
- **Priority**: P3
- **Ticket title format**: `Monthly usage tracking {domain} — {previous month}`
- **Attachment**: Excel report attached to ERP ticket; EPM PDF attached to EPBCS and FCCS-EDM tickets

---

## 3. Slack Notification Settings

- **Channel**: #test-ai
- **When to post**: After Jira tickets are created and files are attached
- **Post title**: Reminder - Monthly Oracle SaaS License Usage
- **Header line**: Assignees please take action: {ticket links and @mentions}

---

## 4. Monthly Usage Report Reminder (runs 6th of each month)

The bot downloads Oracle SaaS usage reports from OCI Object Storage and posts a summary to Slack with Jira ticket links.

**What is included in the Slack post:**
- ERP usage: 25 Oracle Fusion services, 3 rolling months, utilization %
- EPBCS usage: Hosted Named User (B91074) aggregate + per-environment breakdown (PLAN instances)
- FCCS-EDM usage: Records (B91920) + Hosted Environment (B91077) aggregate + per-environment breakdown (EDM, FCCS, EPCM, ESS, NRCS instances)

**Report period note:** ERP and EPM reports are on different Oracle upload schedules and may cover different 3-month windows. The bot displays the upload date and coverage period for each report at the top of the Slack post.

---

## 5. Additional Reminders

Add any other reminders the bot should send below. Each reminder should specify:
- **What**: what the reminder is about
- **When**: when to send it (e.g., monthly on the 6th, quarterly, one-off)
- **Channel**: which Slack channel to post to
- **Who**: who to @mention or assign a Jira ticket to (if applicable)
- **Message**: what the reminder should say

---

### Reminder 2: _(add your reminder here)_

- **What**:
- **When**:
- **Channel**:
- **Who**:
- **Message**:

---

### Reminder 3: _(add your reminder here)_

- **What**:
- **When**:
- **Channel**:
- **Who**:
- **Message**:

---

## 6. Notes for Future Changes

- To change the Slack channel, update section 3 and the `SLACK_CHANNEL` env var in `/etc/fbs-admin/secrets.env`
- To change Jira assignees, update the table in section 1 — the bot will resolve emails to account IDs automatically
- To add a new Oracle report type, add a new section under section 4 and a corresponding entry in `download_saas_usage.sh` and `parse_and_notify.py`
