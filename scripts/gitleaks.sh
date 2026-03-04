#!/bin/bash

# ── Use local tools if not already on PATH from pipeline.sh ─────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$SCRIPT_DIR/../tools"
if [ -d "$TOOLS_DIR" ]; then
    export PATH="$TOOLS_DIR:$PATH"
fi

# ===============================
# Configuration
# ===============================

: "${REPORTS_DIR:=reports}"
: "${STAGE:=gitleaks}"
APP_DIR="${1:-.}"

REPORT_DIR="${REPORTS_DIR}/${STAGE}"
REPORT_FILE="${REPORT_DIR}/result.json"
LOG_FILE="${REPORT_DIR}/${STAGE}.json"

mkdir -p "${REPORT_DIR}"

# ===============================
# Check tool
# ===============================

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "Gitleaks not installed"
    exit 2
fi

# ===============================
# Helpers (avoid relying on 'date' which may be missing in Git Bash)
# ===============================

_PY="$TOOLS_DIR/.venv/Scripts/python"
_ts_ms()  { "$_PY" -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo $(( SECONDS * 1000 )); }
_ts_iso() { "$_PY" -c "import datetime; print(datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'))" 2>/dev/null || echo "unknown"; }

# ===============================
# Execution
# ===============================

START_MS=$(_ts_ms)
EXIT_CODE=0

# Write a temporary .gitleaks.toml config to exclude non-source directories.
# .gitleaksignore is for finding fingerprints only — path exclusions need a config.
_CONFIG_EXISTED=false
[ -f "${APP_DIR}/.gitleaks.toml" ] && _CONFIG_EXISTED=true
cat > "${APP_DIR}/.gitleaks.toml" <<'TOML'
title = "pipeline-scan"

[allowlist]
  description = "Skip generated/build output directories"
  paths = [
    '''reports''',
    '''target''',
    '''\.mvn''',
  ]
TOML

gitleaks detect \
    --source "${APP_DIR}" \
    --no-git \
    --max-target-megabytes 5 \
    --report-format json \
    --report-path "${LOG_FILE}" 2>&1 || EXIT_CODE=$?

# Clean up config file unless one already existed
if [ "$_CONFIG_EXISTED" = false ]; then
    rm -f "${APP_DIR}/.gitleaks.toml"
fi
rm -f "${APP_DIR}/.gitleaksignore"

# ===============================
# Status Handling
# ===============================

if [ "$EXIT_CODE" -eq 0 ]; then
    STATUS="SUCCESS"
    MESSAGE="No secrets detected"
elif [ "$EXIT_CODE" -eq 1 ]; then
    STATUS="SUCCESS"
    MESSAGE="Secrets detected, see ${LOG_FILE} for details"
else
    STATUS="FAILED"
    MESSAGE="Gitleaks execution error (exit code ${EXIT_CODE})"
fi

# ===============================
# Metrics
# ===============================

END_MS=$(_ts_ms)
DURATION_MS=$((END_MS - START_MS))

TIMESTAMP=$(_ts_iso)

# ===============================
# Generate Final Report
# ===============================

printf '{\n  "stage": "%s",\n  "status": "%s",\n  "message": "%s",\n  "exit_code": %s,\n  "timestamp": "%s",\n  "duration_ms": %s,\n  "log_file": "%s"\n}\n' \
    "${STAGE}" "${STATUS}" "${MESSAGE}" "${EXIT_CODE}" "${TIMESTAMP}" "${DURATION_MS}" "${LOG_FILE}" \
    > "${REPORT_FILE}"

echo "Gitleaks stage completed with status: ${STATUS}"
echo "Report generated at: ${REPORT_FILE}"

exit ${EXIT_CODE}