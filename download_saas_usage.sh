#!/bin/bash
# Downloads Oracle SaaS Usage Metrics reports (ERP + EPM) from OCI Object Storage.
# ERP: Oracle uploads between the 4th-7th of each month; covers 3 rolling months.
# EPM: Oracle uploads at end of previous month (PDF format).
# Schedule: run on the 6th of each month.

NAMESPACE="bling"
BUCKET="SAAS-ocid1.tenancy.oc1..aaaaaaaabyrti2yviqcvldyebeyjh5kf7q2zqhujusxfuflvjhx3xqsllvgq"
DOWNLOAD_DIR="${SAAS_USAGE_DIR:-/Users/sindhun/oci/saas-usage}"
mkdir -p "$DOWNLOAD_DIR"
CURRENT_MONTH=$(date +%Y%m)

# --- ERP (Excel) ---
ERP_PREFIX="SaaS_Service_Usage_Metrics_Drill_Through_HNU_Customer_ehsg"
echo "$(date): Looking for ERP report for month $CURRENT_MONTH..."

ERP_LATEST=$(oci os object list \
  --namespace "$NAMESPACE" \
  --bucket-name "$BUCKET" \
  --prefix "$ERP_PREFIX" \
  --query 'data[*].name' \
  --output json | python3 -c "
import json, sys
files = json.load(sys.stdin)
current_month = '${CURRENT_MONTH}'
matches = [f for f in files if f[-13:-5].startswith(current_month)]
print(sorted(matches)[-1] if matches else '')
")

if [ -z "$ERP_LATEST" ]; then
  echo "ERROR: No ERP report found for $CURRENT_MONTH. Oracle may not have uploaded it yet."
  exit 1
fi

ERP_DEST="$DOWNLOAD_DIR/$ERP_LATEST"
if [ -f "$ERP_DEST" ]; then
  echo "Already downloaded: $ERP_LATEST"
else
  echo "Downloading: $ERP_LATEST"
  oci os object get \
    --namespace "$NAMESPACE" \
    --bucket-name "$BUCKET" \
    --name "$ERP_LATEST" \
    --file "$ERP_DEST"
  echo "Saved to: $ERP_DEST"
fi

# --- EPM (PDF) ---
EPM_PREFIX="SaaS_Service_Usage_Metrics_EPM_ehsg"
echo "$(date): Looking for latest EPM report..."

EPM_LATEST=$(oci os object list \
  --namespace "$NAMESPACE" \
  --bucket-name "$BUCKET" \
  --prefix "$EPM_PREFIX" \
  --query 'data[*].name' \
  --output json | python3 -c "
import json, sys
files = json.load(sys.stdin)
print(sorted(files)[-1] if files else '')
")

if [ -z "$EPM_LATEST" ]; then
  echo "WARNING: No EPM report found in bucket."
else
  EPM_DEST="$DOWNLOAD_DIR/$EPM_LATEST"
  if [ -f "$EPM_DEST" ]; then
    echo "Already downloaded: $EPM_LATEST"
  else
    echo "Downloading: $EPM_LATEST"
    oci os object get \
      --namespace "$NAMESPACE" \
      --bucket-name "$BUCKET" \
      --name "$EPM_LATEST" \
      --file "$EPM_DEST"
    echo "Saved to: $EPM_DEST"
  fi
fi
