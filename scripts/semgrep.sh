#!/bin/bash

# ===============================
# Configuration
# ===============================

: "${REPORTS_DIR:=reports}"
: "${STAGE:=semgrep}"
APP_DIR="${1:-.}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/../tools/.venv"

REPORT_DIR="${REPORTS_DIR}/${STAGE}"
REPORT_FILE="${REPORT_DIR}/result.json"
LOG_FILE="${REPORT_DIR}/${STAGE}.json"

mkdir -p "${REPORT_DIR}"

# ===============================
# Check venv
# ===============================

if [ ! -f "${VENV_DIR}/Scripts/activate" ]; then
    echo "Python venv not found in ${VENV_DIR}"
    exit 2
fi

# Activer le venv Windows
source "${VENV_DIR}/Scripts/activate" 2>/dev/null

# ===============================
# Check Semgrep
# ===============================

if ! command -v semgrep >/dev/null 2>&1; then
    echo "Semgrep not installed in venv ${VENV_DIR}"
    exit 2
fi

# ===============================
# Helpers (avoid relying on 'date' which may be missing in Git Bash + venv)
# ===============================

_PY="$SCRIPT_DIR/../tools/.venv/Scripts/python"
_ts_ms()  { "$_PY" -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo $(( SECONDS * 1000 )); }
_ts_iso() { "$_PY" -c "import datetime; print(datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'))" 2>/dev/null || echo "unknown"; }

# ===============================
# Execution
# ===============================

START_MS=$(_ts_ms)
EXIT_CODE=0

semgrep --config=p/java \
        --json \
        --output "${LOG_FILE}" \
        --include "**/*.java" \
        --no-git-ignore \
        "${APP_DIR}" 2>&1 || EXIT_CODE=$?
# ===============================
# Status Handling
# ===============================

if [ "$EXIT_CODE" -eq 0 ]; then
    STATUS="SUCCESS"
    MESSAGE="No issues found"
elif [ "$EXIT_CODE" -eq 1 ]; then
    STATUS="SUCCESS"
    MESSAGE="Issues found, see ${LOG_FILE} for details"
elif [ "$EXIT_CODE" -eq 2 ]; then
    STATUS="FAILED"
    MESSAGE="Semgrep execution error"
else
    STATUS="FAILED"
    MESSAGE="Unknown exit code ${EXIT_CODE}"
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

echo "Semgrep stage completed with status: ${STATUS}"
echo "Report generated at: ${REPORT_FILE}"

# Désactiver le venv
deactivate || true

exit ${EXIT_CODE}