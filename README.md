# oci-admin-bot

Automation scripts for OCI and Oracle SaaS administration. **GitHub Actions workflows are disabled — run scripts locally.**

---

## Scripts

| Script | What it does |
|--------|-------------|
| `slack_refresh_monitor.py` | Monitors a Slack channel for `@fbs-admin` commands and manages OCI environment refresh scheduling |
| `dev2_refresh.py` | Triggers an OCI Fusion environment refresh directly |
| `download_saas_usage.sh` | Downloads monthly Oracle SaaS usage reports (ERP Excel + EPM PDF) from OCI Object Storage |
| `parse_and_notify.py` | Parses downloaded reports, creates Jira tickets, and posts a usage summary to Slack |
| `oci_access_review.py` | Runs a quarterly OCI user access review and posts results to Slack |
| `maintenance_reminder.py` | Posts quarterly Oracle maintenance reminders to Slack |
| `quarterly_release_reminder.py` | Posts quarterly Oracle release reminders to Slack |

---

## Local Setup

### 1. Prerequisites

- Python 3.9+
- OCI CLI installed and configured (`~/.oci/config` + PEM key)

### 2. Clone and install dependencies

```bash
git clone https://github.com/snac8/oci-admin-bot.git
cd oci-admin-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set environment variables

Create a local file (e.g. `secrets.env`) — **never commit this**:

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_CHANNEL=#your-channel
export SLACK_CHANNEL_ID=C0ALU5462EB

# OCI environment OCIDs
export DEV2_OCID=ocid1.fusionenvironment.oc1.iad...
export PROD_OCID=ocid1.fusionenvironment.oc1.iad...

# For parse_and_notify.py
export SAAS_USAGE_DIR=/path/to/local/saas-usage
export JIRA_EMAIL=you@block.xyz
export JIRA_TOKEN=ATATT3x...
export JIRA_ASSIGNEE_ERP=<jira-account-id>
export JIRA_ASSIGNEE_EPBCS=<jira-account-id>
export JIRA_ASSIGNEE_FCCS_EDM=<jira-account-id>
export SLACK_USER_ERP=ramesh@block.xyz
export SLACK_USER_EPBCS=sahle@block.xyz
export SLACK_USER_FCCS_EDM=abbas@block.xyz
```

Then source it:

```bash
source secrets.env
```

---

## Running Scripts

### Slack Refresh Monitor

Polls the Slack channel every 15 minutes for `@fbs-admin` commands. Handles refresh scheduling, cancellation, status queries, and auto-discovery of scheduled OCI activities.

```bash
python3 slack_refresh_monitor.py
```

**Supported Slack commands** (in the monitored channel):
```
@fbs-admin refresh <env-url> on <date> [<time>] [PT]
@fbs-admin when is the next refresh of dev2?
@fbs-admin what's scheduled for dev2?
```

**Supported thread commands** (reply to an existing refresh thread):
```
@fbs-admin cancel
@fbs-admin reschedule to <date> <time> PT
@fbs-admin status
```

Env vars required: `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `DEV2_OCID`, `PROD_OCID`

---

### Dev2 Refresh

Triggers an OCI Fusion environment refresh directly.

```bash
python3 dev2_refresh.py
```

Env vars required: `DEV2_OCID`, `PROD_OCID`

---

### Monthly Oracle SaaS Usage Report

**Step 1 — Download reports from OCI:**

```bash
bash download_saas_usage.sh
```

Downloads ERP (Excel) and EPM (PDF) files to `$SAAS_USAGE_DIR` (default: `~/oci/saas-usage`).

**Step 2 — Parse, create Jira tickets, and post to Slack:**

```bash
python3 parse_and_notify.py
```

Env vars required: `SLACK_BOT_TOKEN`, `SLACK_CHANNEL`, `SAAS_USAGE_DIR`, `JIRA_EMAIL`, `JIRA_TOKEN`, plus assignee vars.

---

### OCI Access Review

```bash
python3 oci_access_review.py
```

Env vars required: `SLACK_BOT_TOKEN`, `SLACK_CHANNEL`

---

### Maintenance / Release Reminders

```bash
python3 maintenance_reminder.py
python3 quarterly_release_reminder.py
```

Env vars required: `SLACK_BOT_TOKEN`, `SLACK_CHANNEL`

---

## OCI CLI Config

The scripts rely on a working OCI CLI config at `~/.oci/config`:

```ini
[DEFAULT]
user=ocid1.user.oc1...<your-user-ocid>
fingerprint=<key-fingerprint>
key_file=~/.oci/your-key.pem
tenancy=ocid1.tenancy.oc1...<your-tenancy-ocid>
region=us-ashburn-1
```

---

## Security Notes

- `saas-usage/` and `*.pem` are excluded from git
- Never commit `secrets.env` or any file containing tokens or OCIDs
