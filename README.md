# FBS Admin — Oracle SaaS License Usage Reporting

## Application Summary

The FBS Admin pipeline is an automated monthly reporting system that monitors Oracle SaaS license consumption for Block, Inc across ERP and EPM cloud services. It downloads usage reports from OCI Object Storage, parses them, creates Jira tracking tickets, and posts a formatted summary to Slack — all triggered by a cron job on the 6th of each month.

### Key Features
- **Dual Report Processing**: Handles both ERP (Excel) and EPM (PDF) Oracle SaaS usage reports
- **OCI Object Storage Integration**: Automatically discovers and downloads the latest monthly reports from Oracle's delivery bucket
- **Jira Ticket Automation**: Creates one ticket per domain (ERP, EPBCS, FCCS-EDM) in the FBS project with usage data in the description and the source file attached
- **Slack Notifications**: Posts a combined usage summary to `#test-ai` with Jira ticket links, assignee @mentions, and 90%+ utilization alerts
- **Scheduled Execution**: Runs on the 6th of each month via cron, after Oracle's upload window (4th–7th)
- **Secret Management**: Credentials stored outside the repo in `/etc/fbs-admin/secrets.env`

---

## Application Flows

### 1. Download Flow — `download_saas_usage.sh`

**Purpose**: Downloads the current month's ERP Excel report and the latest EPM PDF report from OCI Object Storage.

**ERP (Excel)**
- Queries the OCI bucket with prefix `SaaS_Service_Usage_Metrics_Drill_Through_HNU_Customer_ehsg`
- Filters for files whose embedded date starts with the current `YYYYMM`
- Skips download if the file already exists locally
- Exits with error if Oracle has not yet uploaded the file

**EPM (PDF)**
- Queries the same OCI bucket with prefix `SaaS_Service_Usage_Metrics_EPM_ehsg`
- Downloads the latest file by filename sort order
- Skips download if already present locally

**OCI Config**:
- Namespace: `bling`
- Bucket: `SAAS-ocid1.tenancy.oc1..aaaaaaaabyrti2yviqcvldyebeyjh5kf7q2zqhujusxfuflvjhx3xqsllvgq`
- Files saved to `$SAAS_USAGE_DIR` (default: `/Users/sindhun/oci/saas-usage`)

---

### 2. Parse & Notify Flow — `parse_and_notify.py`

#### 2.1 ERP Report Parsing
**Source**: `SaaS_Service_Usage_Metrics_Drill_Through_HNU_Customer_ehsg_YYYYMMDD.xlsx`
- Reads the `Usage Summary` sheet
- Extracts 3 rolling months of usage, subscribed quantity, remaining, and utilization % for all 25 Oracle Fusion services
- Flags any service at 90%+ utilization for alert

#### 2.2 EPM Report Parsing
**Source**: `SaaS_Service_Usage_Metrics_EPM_ehsg_YYYYMMDD.pdf`
- Scans all PDF pages for tables containing `Subscription Utilization` columns
- Extracts 3 rolling months of usage, subscribed quantity, remaining, and utilization %
- Splits services by part number:
  - `B91074` (Hosted Named User) → **EPBCS**
  - `B91920` (EDM Records) + `B91077` (Hosted Environment) → **FCCS-EDM**

#### 2.3 Jira Ticket Creation
Creates one Task per domain in project **FBS** with:

| Ticket | Component | Assignee (prod) | Attachment |
|--------|-----------|-----------------|------------|
| `Monthly usage tracking ERP — {month}` | `usagetracking-ERP` | Ramesh Koduri | Excel report |
| `Monthly usage tracking EPBCS — {month}` | `usagetracking-EPBCS` | Sahle Melaku | EPM PDF |
| `Monthly usage tracking FCCS-EDM — {month}` | `usagetracking-FCCS-EDM` | Abbas Ali Mogal | EPM PDF |

Each ticket description includes a formatted usage table for the relevant services.

#### 2.4 Slack Notification
Posts a single combined message to `$SLACK_CHANNEL` containing:
- Jira ticket links for all 3 domains
- @mention of each assignee
- ERP usage table (25 services, 3 rolling months)
- EPM/EPBCS usage table
- EPM/FCCS-EDM usage table
- 90%+ utilization alert section (if applicable)

---

### 3. Orchestration — `cron/run_monthly.sh`

Runs on the 6th of each month at 08:00 UTC via `cron/fbs-admin.cron`:
1. Sources `/etc/fbs-admin/secrets.env`
2. Runs `download_saas_usage.sh`
3. Runs `parse_and_notify.py` via the Python venv

Logs to `/var/log/fbs-admin.log`.

---

## Setup

### Prerequisites
- Python 3.9+
- OCI CLI configured (`~/.oci/config` + PEM key)
- Slack bot with scopes: `chat:write`, `chat:write.public`, `users:read`, `users:read.email`
- Jira API token for `sindhun@block.xyz`

### Deploy to OCI Compute (Ubuntu, Ampere A1 free tier)

```bash
bash deploy/setup_oci_compute.sh https://github.com/snac8/fbs-admin.git
```

This installs system dependencies, OCI CLI, clones the repo, sets up the Python venv, and installs the cron job.

### Manual steps after deploy

1. Copy OCI config and PEM key to `~/.oci/`
2. Create `/etc/fbs-admin/secrets.env`:

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_CHANNEL=#your-channel
export SAAS_USAGE_DIR=/home/ubuntu/oci/saas-usage
export JIRA_EMAIL=sindhun@block.xyz
export JIRA_TOKEN=ATATT3x...
export JIRA_ASSIGNEE_ERP=<jira-account-id>
export JIRA_ASSIGNEE_EPBCS=<jira-account-id>
export JIRA_ASSIGNEE_FCCS_EDM=<jira-account-id>
export SLACK_USER_ERP=ramesh@block.xyz
export SLACK_USER_EPBCS=sahle@block.xyz
export SLACK_USER_FCCS_EDM=abbas@block.xyz
```

```bash
sudo chmod 600 /etc/fbs-admin/secrets.env
sudo chown ubuntu /etc/fbs-admin/secrets.env
```

3. Test:
```bash
bash /home/ubuntu/oci/cron/run_monthly.sh
```

---

## Project Structure

```
fbs-admin/
├── download_saas_usage.sh     # OCI download script (ERP + EPM)
├── parse_and_notify.py        # Parse reports, create Jira tickets, post to Slack
├── requirements.txt           # Python dependencies
├── .gitignore                 # Excludes saas-usage/, *.pem, secrets
├── cron/
│   ├── fbs-admin.cron         # Crontab entry (6th of month, 08:00 UTC)
│   └── run_monthly.sh         # Orchestrator script
└── deploy/
    └── setup_oci_compute.sh   # One-time OCI compute setup
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `openpyxl` | Parse Oracle ERP Excel reports |
| `pdfplumber` | Parse Oracle EPM PDF reports |
| `python-dateutil` | Previous month calculation |
| `requests` | Jira REST API + Slack API calls |

---

## Security Notes

- `saas-usage/` is excluded from git (may contain PII)
- OCI PEM keys (`*.pem`) are excluded from git
- All secrets are stored in `/etc/fbs-admin/secrets.env` (mode 600, outside repo)
