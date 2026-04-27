# FBS Admin Bot — Activities Reference

All automated activities performed by the **fbs-admin** Slack bot in **#test-ai**.

---

## Activity Summary

| # | Activity | Trigger | Channel |
|---|----------|---------|---------|
| 1 | Monthly Oracle SaaS License Usage | 6th of each month | #test-ai |
| 2 | Oracle Dev2 Quarterly Refresh | Quarterly (15th of Jan/Apr/Jul/Oct) | #test-ai |
| 3 | On-Demand Refresh via Slack | `@fbs-admin` mention anytime | #test-ai |
| 4 | Automatic Refresh Reminders (all envs) | Every 15 minutes | #test-ai |
| 5 | Quarterly Upgrade Maintenance Reminder | Daily at 9am PT | #test-ai |
| 6 | Oracle Quarterly Release Summary (KLO) | 1st of Jan/Apr/Jul/Oct at 9am PT | #test-ai |

---

## 1. Monthly Oracle SaaS License Usage

**Script:** `parse_and_notify.py`
**Schedule:** 6th of each month (GitHub Actions: `.github/workflows/saas-usage.yml`)

### What it does
- Downloads the latest ERP (Excel) and EPM (PDF) usage reports from OCI Object Storage
- Parses 25 ERP Fusion services with 3-month rolling utilization %
- Parses EPBCS (Hosted Named User B91074) and FCCS-EDM (Records B91920 + Hosted Environment B91077)
- Creates **3 Jira tickets** in project FBS:

| Domain | Assignee | Email |
|--------|----------|-------|
| ERP | Ramesh Koduri | ramesh@block.xyz |
| EPBCS | Sahle Melaku | smelaku@block.xyz |
| FCCS-EDM | Abbas Ali Mogal | abbas@block.xyz |

- **Jira settings:** Project FBS · Epic [FBS-21188](https://block.atlassian.net/browse/FBS-21188) · Component: Access - Oracle · Priority: P2 · Sprint: FBS Operations Support
- **Title format:** `Monthly Oracle SaaS Usage Tracking - {domain} - {month}`
- Attaches the Excel report to the ERP ticket; PDF to EPBCS and FCCS-EDM tickets
- Posts combined usage summary to **#test-ai** with:
  - ERP service utilization table (3 rolling months)
  - EPBCS aggregate + per-environment breakdown
  - FCCS-EDM aggregate + per-environment breakdown
  - ⚠ alerts for any service at ≥90% utilization

---

## 2. Oracle Dev2 Quarterly Refresh

**Script:** `dev2_refresh.py`
**Schedule:** GitHub Actions: `.github/workflows/dev2-refresh.yml`

### Schedule
| When | Action |
|------|--------|
| 1 week before the 15th (8th, or Thursday if weekend) | Posts 1-week reminder to #test-ai + submits OCI refresh (prod → dev2, 5:00 PM PT) |
| Day before refresh at 9am PT | Posts 24-hour advance notice to #test-ai |

### Run modes (manual trigger via `workflow_dispatch`)
| Mode | Description |
|------|-------------|
| `check-reminder` | Post reminder + submit OCI only if today is the right day |
| `check-notify` | Post 24-hour notice only if today is the right day |
| `force-reminder` | Always post reminder + submit OCI refresh |
| `force-submit` | Submit OCI refresh only |
| `force-notify` | Always post 24-hour notice |

**Weekend rule:** if the 15th or 8th falls on Saturday or Sunday, action runs on the Thursday of that week instead.

---

## 3. On-Demand Refresh via Slack

**Script:** `slack_refresh_monitor.py`
**Schedule:** Runs every 15 minutes (GitHub Actions: `.github/workflows/refresh-monitor.yml`)

### Channel commands (top-level `@fbs-admin` mention)

| Command | Action |
|---------|--------|
| `@fbs-admin refresh <url> on <date> [time] PT` | Submits OCI refresh for the named environment |
| `@fbs-admin when is the next refresh of dev1?` | Lists all scheduled refreshes for that environment |
| `@fbs-admin status dev2` | Shows all active refresh activities with scheduled times |

If no time is specified, defaults to **5:00 PM PT**.

### Thread commands (reply in any refresh notification thread)

| Command | Action |
|---------|--------|
| `@fbs-admin cancel` | Cancels all active refresh activities for that environment |
| `@fbs-admin reschedule to <date> <time> PT` | Cancels existing and submits new refresh at specified time |
| `@fbs-admin reschedule it for <date>` | Same — flexible phrasing supported |
| `@fbs-admin when is this scheduled?` | Shows current scheduled time and activity ID |

All commands reply in-thread with Request ID, scheduled date/time, and environment URL.

---

## 4. Automatic Refresh Reminders (All Environments)

**Script:** `slack_refresh_monitor.py`
**Schedule:** Every 15 minutes (same workflow as activity 3)

### What it does
- Scans **all 5 environments** (dev1–dev4, test) every run for any active OCI refresh activity
- Works for refreshes submitted via this bot **or** directly in the OCI console
- Tracks each activity in OCI Object Storage state (bucket: `fbs-admin-state`) so reminders persist across runs

### Reminder timeline

| Timing | Action |
|--------|--------|
| 6–8 days before scheduled time | Posts 1-week reminder to #test-ai |
| Within 24 hours of scheduled time | Posts 24-hour reminder to #test-ai + replies in 1-week thread |
| After refresh completes (SUCCEEDED) | Posts completion notification with timestamp and activity ID |

---

## 5. Quarterly Upgrade Maintenance Reminder

**Script:** `maintenance_reminder.py`
**Schedule:** Daily at 9am PT (GitHub Actions: `.github/workflows/maintenance-reminder.yml`)

### What it does
- Queries all 6 environments for upcoming `QUARTERLY_UPGRADE` scheduled activities
- Posts reminder to **#test-ai** when maintenance is **within 7 days**
- Groups environments that share the same maintenance window (within 1 hour)
- Environments with different maintenance windows (e.g., prod vs dev) get separate reminders

### Slack post includes
- Maintenance window date/time in **PT (PDT/PST)**
- Oracle update name (e.g., "Fusion Applications Update 26B")
- List of all affected environment URLs

### Run modes (manual trigger)
| Mode | Description |
|------|-------------|
| `check` | Post only if maintenance is within 7 days (default) |
| `force` | Always post regardless of timing (for testing) |

---

## Environments

| Name | URL | Used for Refresh | Used for Maintenance |
|------|-----|:---:|:---:|
| prod | https://ehsg.fa.us6.oraclecloud.com/fscmUI/faces/FuseOverview | ✗ | ✓ |
| dev1 | https://ehsg-dev1.login.us6.oraclecloud.com/ | ✓ | ✓ |
| dev2 | https://ehsg-dev2.login.us6.oraclecloud.com/ | ✓ | ✓ |
| dev3 | https://ehsg-dev3.fa.ocs.oraclecloud.com/fscmUI/faces/FuseOverview | ✓ | ✓ |
| dev4 | https://ehsg-dev4.fa.ocs.oraclecloud.com/fscmUI/faces/FuseOverview | ✓ | ✓ |
| test | https://ehsg-test.fa.us6.oraclecloud.com/fscmUI/faces/FuseOverview | ✓ | ✓ |

---

## 6. Oracle Quarterly Release Summary (KLO)

**Script:** `quarterly_release_reminder.py`
**Schedule:** 1st of January, April, July, October at 9am PT (GitHub Actions: `.github/workflows/quarterly-release-reminder.yml`)

### What it does
- Creates a Jira ticket in project **FBSPROJ** for the Oracle quarterly release summary
- Posts a Slack reminder to **#test-ai** mentioning Jinesh Kumar

### Jira ticket settings

| Field | Value |
|-------|-------|
| Project | FBSPROJ |
| Title | `KLO: {quarter} Release Summary` (e.g. KLO: 26B Release Summary) |
| Issue type | Task |
| Priority | P3 |
| Component | Controllership |
| Parent epic | [FBSPROJ-2135](https://block.atlassian.net/browse/FBSPROJ-2135) — Oracle Quarterly Releases |
| Assignee | Jinesh Kumar (jinesh@block.xyz) |

### Oracle quarter labels

| Month | Label | Example |
|-------|-------|---------|
| January | A | 26A |
| April | B | 26B |
| July | C | 26C |
| October | D | 26D |

### Run modes (manual trigger)
| Mode | Description |
|------|-------------|
| `check` | Create ticket + post only if today is the 1st of a quarter month (default) |
| `force` | Always create ticket + post (for testing) |

---

## Infrastructure

| Item | Value |
|------|-------|
| GitHub repo | github.com/snac8/oci-admin-bot |
| OCI state bucket | `axbix6knqxie / fbs-admin-state` |
| Jira project (SaaS usage) | FBS — epic [FBS-21188](https://block.atlassian.net/browse/FBS-21188) Oracle SaaS Service Usage Review and Cleanup |
| Jira project (quarterly release) | FBSPROJ — epic [FBSPROJ-2135](https://block.atlassian.net/browse/FBSPROJ-2135) Oracle Quarterly Releases |
| Slack channel | #test-ai (C0ALU5462EB) |
| Secrets location | `/etc/fbs-admin/secrets.env` |
