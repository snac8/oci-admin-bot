#!/bin/bash
# Downloads the Oracle SaaS Usage Metrics report for the current month from OCI Object Storage
# Oracle uploads the file between the 4th-7th of each month.
# Schedule: run on the 8th of each month to ensure the file is available.
# Each file covers 3 rolling months of usage data (e.g. the 0301 file covers Dec/Jan/Feb).

NAMESPACE="bling"
BUCKET="SAAS-ocid1.tenancy.oc1..aaaaaaaabyrti2yviqcvldyebeyjh5kf7q2zqhujusxfuflvjhx3xqsllvgq"
DOWNLOAD_DIR="${SAAS_USAGE_DIR:-/Users/sindhun/oci/saas-usage}"
mkdir -p "$DOWNLOAD_DIR"
PREFIX="SaaS_Service_Usage_Metrics_Drill_Through_HNU_Customer_ehsg"
CURRENT_MONTH=$(date +%Y%m)

echo "$(date): Looking for report for month $CURRENT_MONTH..."

# Find the file uploaded this month (filename date starts with current YYYYMM)
LATEST=$(oci os object list \
  --namespace "$NAMESPACE" \
  --bucket-name "$BUCKET" \
  --prefix "$PREFIX" \
  --query 'data[*].name' \
  --output json | python3 -c "
import json, sys
files = json.load(sys.stdin)
# File date is embedded as last 8 chars before .xlsx e.g. _20260301.xlsx
current_month = '${CURRENT_MONTH}'
matches = [f for f in files if f[-13:-5].startswith(current_month)]
print(sorted(matches)[-1] if matches else '')
")

if [ -z "$LATEST" ]; then
  echo "ERROR: No report found for $CURRENT_MONTH. Oracle may not have uploaded it yet."
  exit 1
fi

DEST="$DOWNLOAD_DIR/$LATEST"

if [ -f "$DEST" ]; then
  echo "Already downloaded: $LATEST"
  exit 0
fi

echo "Downloading: $LATEST"
oci os object get \
  --namespace "$NAMESPACE" \
  --bucket-name "$BUCKET" \
  --name "$LATEST" \
  --file "$DEST"

echo "Saved to: $DEST"
echo "This report covers 3 rolling months of usage data."
