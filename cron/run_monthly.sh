#!/bin/bash
set -euo pipefail

# Load secrets from outside the repo (never committed to git)
source /etc/fbs-admin/secrets.env

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO_DIR/.venv"

echo "=== $(date): Starting fbs-admin monthly run ==="

# Step 1: Download the latest Excel report
bash "$REPO_DIR/download_saas_usage.sh"

# Step 2: Parse and post to Slack
"$VENV/bin/python3" "$REPO_DIR/parse_and_notify.py"

echo "=== $(date): Done ==="
