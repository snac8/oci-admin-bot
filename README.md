# oci-admin-bot

Automation scripts for Oracle Cloud Infrastructure (OCI) and Oracle SaaS administration. Posts maintenance reminders to Slack, creates Jira tickets for license usage and access reviews, and accepts ad-hoc refresh commands via Slack `@mention`.

Designed to run locally as scheduled cron jobs against any Oracle Fusion / OCI tenant. All organization-specific values are pulled from environment variables — no secrets or identifiers are baked into the source.

---

## Scripts

| Script | What it does |
|--------|-------------|
| `slack_refresh_monitor.py` | Polls a Slack channel for `@oci-admin` commands. Schedules / cancels / reschedules OCI Fusion environment refreshes. Auto-discovers refresh activities (UI- or bot-submitted) and posts 1-week / 24-hour / completion notifications. |
| `dev2_refresh.py` | Submits a one-shot OCI Fusion environment refresh (typically prod → a non-prod target) and posts reminders. |
| `parse_and_notify.py` | Parses Oracle SaaS license-usage reports (ERP Excel + EPM PDF), creates Jira tickets per license domain, attaches reports, and posts a Slack summary with utilization alerts. |
| `oci_access_review.py` | Quarterly OCI user access review. Exports the IDCS user list to CSV, creates a Jira task, attaches the CSV, and posts a Slack reminder. |
| `maintenance_reminder.py` | Daily check for upcoming Oracle Fusion `QUARTERLY_UPGRADE` activities across all configured environments. Posts at the 7-day and 24-hour windows plus a completion notification. |
| `quarterly_release_reminder.py` | Quarterly: creates a Jira tracking task for Oracle quarterly release work and posts a Slack reminder. |

---

## Setup

### 1. Prerequisites

- Python 3.9+
- [OCI CLI](https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm) installed and configured at `~/.oci/config`
- A Slack app installed in your workspace with at minimum these scopes: `chat:write`, `channels:history`, `groups:history`, `app_mentions:read`, `files:write`
- A Jira API token if you use the Jira-integrating scripts ([create one here](https://id.atlassian.com/manage-profile/security/api-tokens))

### 2. Clone and install

```bash
git clone https://github.com/snac8/oci-admin-bot.git
cd oci-admin-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

Copy `.env.example` to a local file (e.g. `secrets.env`) and fill in your values. **Never commit it.**

```bash
cp .env.example secrets.env
$EDITOR secrets.env
source secrets.env
```

The full set of environment variables is documented in `.env.example`. The most important ones:

| Variable | Used by | Purpose |
|---|---|---|
| `OCI_ADMIN_SLACK_BOT_TOKEN` | all | Slack bot OAuth token (`xoxb-…`) |
| `OCI_ADMIN_SLACK_CHANNEL` | all | Slack channel name (e.g. `#oci-admin`) |
| `OCI_ADMIN_SLACK_CHANNEL_ID` | slack_refresh_monitor | Slack channel ID (e.g. `C0…`); needed for `conversations.history` |
| `OCI_ADMIN_OCI_REGION` | all | OCI region (e.g. `us-ashburn-1`); defaults to `us-ashburn-1` |
| `PROD_OCID`, `DEV1_OCID`, `DEV2_OCID`, `DEV3_OCID`, `DEV4_OCID`, `TEST_OCID` | maintenance_reminder, slack_refresh_monitor, dev2_refresh | Fusion environment OCIDs to monitor / refresh |
| `OCI_ADMIN_JIRA_EMAIL` | parse_and_notify, oci_access_review, quarterly_release_reminder | Email used to authenticate against Jira API |
| `OCI_ADMIN_JIRA_TOKEN` | same | Jira API token |
| `OCI_ADMIN_JIRA_PROJECT` | same | Jira project key (e.g. `OPS`) |
| `OCI_ADMIN_JIRA_COMPONENT` | same | Jira component ID for created tickets |
| `OCI_ADMIN_JIRA_SPRINT` | same | *Optional*: numeric sprint ID for created tickets |
| `OCI_ADMIN_ASSIGNEE_ACCOUNT_ID` | oci_access_review, quarterly_release_reminder | Jira account ID of the default assignee |
| `OCI_ADMIN_ASSIGNEE_EMAIL` | same | Email of the default assignee (used for Slack `@mention` lookup) |
| `OCI_ADMIN_IDCS_ENDPOINT` | oci_access_review | Your IDCS instance URL, e.g. `https://idcs-XXXXX.identity.oraclecloud.com` |
| `OCI_ADMIN_SAAS_USAGE_DIR` | parse_and_notify | Local directory containing downloaded ERP/EPM report files |

### 4. OCI CLI

`~/.oci/config` should look like this:

```ini
[DEFAULT]
user=ocid1.user.oc1..<your-user-ocid>
fingerprint=<your-key-fingerprint>
key_file=/path/to/oci_api_key.pem
tenancy=ocid1.tenancy.oc1..<your-tenancy-ocid>
region=us-ashburn-1
```

Verify with:

```bash
oci iam user get --user-id "$(oci iam user list --query 'data[0].id' --raw-output)"
```

---

## Running

Each script is independent. Most accept `--help` and a `--mode` flag (`check` for normal scheduled runs, `force` for ad-hoc / testing).

```bash
# Slack refresh monitor — typically scheduled every minute via cron
python3 slack_refresh_monitor.py

# One-shot dev2 refresh
python3 dev2_refresh.py --mode force-submit

# Maintenance reminder — typically scheduled daily
python3 maintenance_reminder.py --mode check

# OCI user access review — typically scheduled on the 1st of Jan/Apr/Jul/Oct
python3 oci_access_review.py --mode check

# Quarterly release reminder
python3 quarterly_release_reminder.py --mode check

# Monthly Oracle SaaS license usage report
python3 parse_and_notify.py
```

### Example `crontab`

```cron
*  *  *   *   *      . $HOME/oci-admin-bot/secrets.env && cd $HOME/oci-admin-bot && .venv/bin/python slack_refresh_monitor.py >> /tmp/oci-admin.log 2>&1
0  9  *   *   *      . $HOME/oci-admin-bot/secrets.env && cd $HOME/oci-admin-bot && .venv/bin/python maintenance_reminder.py --mode check >> /tmp/oci-admin.log 2>&1
0  8  6   *   *      . $HOME/oci-admin-bot/secrets.env && cd $HOME/oci-admin-bot && .venv/bin/python parse_and_notify.py >> /tmp/oci-admin.log 2>&1
0 16  1   1,4,7,10 * . $HOME/oci-admin-bot/secrets.env && cd $HOME/oci-admin-bot && .venv/bin/python quarterly_release_reminder.py --mode check >> /tmp/oci-admin.log 2>&1
0 16  2   1,4,7,10 * . $HOME/oci-admin-bot/secrets.env && cd $HOME/oci-admin-bot && .venv/bin/python oci_access_review.py --mode check >> /tmp/oci-admin.log 2>&1
```

(All run times are in your local timezone unless your cron daemon is configured otherwise.)

---

## Slack commands

When `slack_refresh_monitor.py` is running, mention the bot in its channel:

| Command | Action |
|---|---|
| `@oci-admin refresh <env-url> on <date> [time] PT` | Submit an OCI refresh for the matching environment |
| `@oci-admin status <env>` | List active refresh activities for that env |
| `@oci-admin when is the next refresh of <env>?` | Show scheduled refreshes |
| `@oci-admin cancel` *(in a refresh thread)* | Cancel that refresh |
| `@oci-admin reschedule to <date> <time> PT` *(in a refresh thread)* | Cancel & resubmit at the new time |

If a time isn't specified, the bot defaults to 5:00 PM PT.

---

## Disclaimer

This is a personal project published as-is. Use at your own risk against your own Oracle / Slack / Jira tenants. Keep your `secrets.env` file out of version control.

## License

[MIT](LICENSE)
