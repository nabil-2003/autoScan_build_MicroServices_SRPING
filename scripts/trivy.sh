#!/bin/bash

# ===============================
# Configuration
# ===============================

: "${REPORTS_DIR:=reports}"
: "${STAGE:=trivy}"
APP_DIR="${1:-.}"

REPORT_DIR="${REPORTS_DIR}/${STAGE}"
REPORT_FILE="${REPORT_DIR}/result.json"
LOG_FILE="${REPORT_DIR}/${STAGE}.json"

mkdir -p "${REPORT_DIR}"

# ===============================
# Check tool
# ===============================

if ! command -v trivy >/dev/null 2>&1; then
    echo "Trivy not installed"
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

trivy fs "${APP_DIR}" \
      --format json \
      --output "${LOG_FILE}" \
      --skip-dirs "reports" \
      --skip-dirs "target" \
      --skip-dirs ".mvn" 2>&1 || EXIT_CODE=$?

# ===============================
# Status Handling
# ===============================

if [ "$EXIT_CODE" -eq 0 ]; then
    STATUS="SUCCESS"
    MESSAGE="No vulnerabilities found"
elif [ "$EXIT_CODE" -eq 1 ]; then
    STATUS="SUCCESS"
    MESSAGE="Vulnerabilities found, see ${LOG_FILE} for details"
else
    STATUS="FAILED"
    MESSAGE="Trivy execution error (exit code ${EXIT_CODE})"
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

echo "Trivy stage completed with status: ${STATUS}"
echo "Report generated at: ${REPORT_FILE}"

exit ${EXIT_CODE}