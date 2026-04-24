#!/bin/bash
# One-time setup script for OCI Compute (Ubuntu, free tier Ampere A1)
# Run as: bash setup_oci_compute.sh
set -euo pipefail

REPO_URL="${1:-}"  # Pass your git remote URL as first argument
REPO_DIR="/home/ubuntu/oci"

echo "=== Installing system dependencies ==="
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git

echo "=== Installing OCI CLI ==="
bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)" -- --accept-all-defaults

echo "=== Cloning/updating repo ==="
if [ -z "$REPO_URL" ]; then
  echo "ERROR: Pass git remote URL as first argument."
  echo "Usage: bash setup_oci_compute.sh https://github.com/yourorg/fbs-admin.git"
  exit 1
fi

if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" pull
else
  git clone "$REPO_URL" "$REPO_DIR"
fi

echo "=== Setting up Python virtual environment ==="
python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

echo "=== Setting up log file ==="
sudo touch /var/log/fbs-admin.log
sudo chown ubuntu:ubuntu /var/log/fbs-admin.log

echo "=== Installing cron job ==="
chmod +x "$REPO_DIR/cron/run_monthly.sh"
(crontab -l 2>/dev/null | grep -v fbs-admin; cat "$REPO_DIR/cron/fbs-admin.cron") | crontab -

echo ""
echo "=== Setup complete. Manual steps remaining: ==="
echo "  1. Copy OCI config:  mkdir -p ~/.oci && scp your-machine:~/.oci/config ~/.oci/config"
echo "  2. Copy PEM key:     scp your-machine:/Users/sindhun/oci/snac_oci_license_usage_api.pem ~/.oci/"
echo "     Then update key_file path in ~/.oci/config to /home/ubuntu/.oci/snac_oci_license_usage_api.pem"
echo "  3. Create secrets:   sudo mkdir -p /etc/fbs-admin"
echo "                       sudo tee /etc/fbs-admin/secrets.env <<EOF"
echo "                       export SLACK_BOT_TOKEN=xoxb-your-token-here"
echo "                       export SLACK_CHANNEL=#test-ai"
echo "                       export SAAS_USAGE_DIR=/home/ubuntu/oci/saas-usage"
echo "                       EOF"
echo "                       sudo chmod 600 /etc/fbs-admin/secrets.env"
echo "  4. Test:             bash /home/ubuntu/oci/cron/run_monthly.sh"
