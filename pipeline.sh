#!/bin/bash

# ── Resolve the script's own directory so tools/ is always found ────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$SCRIPT_DIR/tools"

# ── Wire all local tools into PATH before anything else ─────────────────────
if [ ! -d "$TOOLS_DIR/jdk17" ] || [ ! -d "$TOOLS_DIR/maven" ]; then
    echo "ERROR: Local tools not found in $TOOLS_DIR"
    echo "       Please run  bash install.sh  first."
    exit 1
fi

export JAVA_HOME="$TOOLS_DIR/jdk17"
export PATH="$TOOLS_DIR/maven/bin:$JAVA_HOME/bin:$TOOLS_DIR:$PATH"

# Fallback opens for any remaining old-plugin reflection (belt-and-suspenders).
# The real fix is the pom-patcher below that upgrades maven-war-plugin to 3.4.0.
export MAVEN_OPTS="\
  --add-opens java.base/java.util=ALL-UNNAMED \
  --add-opens java.base/java.lang=ALL-UNNAMED \
  --add-opens java.base/java.lang.reflect=ALL-UNNAMED \
  --add-opens java.base/java.io=ALL-UNNAMED \
  --add-opens java.base/java.text=ALL-UNNAMED \
  --add-opens java.base/java.net=ALL-UNNAMED \
  --add-opens java.base/java.security=ALL-UNNAMED \
  --add-opens java.base/java.security.cert=ALL-UNNAMED \
  --add-opens java.base/java.nio=ALL-UNNAMED \
  --add-opens java.base/sun.nio.ch=ALL-UNNAMED \
  --add-opens java.base/sun.security.util=ALL-UNNAMED \
  --add-opens java.desktop/java.awt.font=ALL-UNNAMED \
  --add-opens java.desktop/java.awt=ALL-UNNAMED \
  --add-opens java.desktop/java.awt.color=ALL-UNNAMED \
  --add-opens java.desktop/javax.swing=ALL-UNNAMED \
  --add-opens java.sql/java.sql=ALL-UNNAMED"

PROJECT_DIR=${1:-$(pwd)}
REPORTS_DIR="$PROJECT_DIR/reports"

echo "=================================="
echo " Secure Spring Boot Microservices Pipeline"
echo "=================================="
echo "  JAVA_HOME : $JAVA_HOME"
echo "  Maven     : $(which mvn 2>/dev/null || echo 'not found')"
echo "=================================="

chmod +x scripts/*.sh

SERVICES=()

# 🔎 Détection multi-module
if [ -f "$PROJECT_DIR/pom.xml" ]; then
    MODULES=$(grep -oP '(?<=<module>).*?(?=</module>)' "$PROJECT_DIR/pom.xml")

    if [ -n "$MODULES" ]; then
        echo "Multi-module project detected"
        for module in $MODULES; do
            if [ -d "$PROJECT_DIR/$module" ]; then
                SERVICES+=("$PROJECT_DIR/$module")
            fi
        done
    else
        SERVICES+=("$PROJECT_DIR")
    fi
else
    echo "No pom.xml found"
    exit 1
fi

# ===============================
# Generate docker-compose file
# ===============================
echo "Generating docker-compose.generated.yml..."
python ./scripts/auto-docker-generator.py "$PROJECT_DIR"
if [ $? -ne 0 ]; then
    echo "Pipeline stopped: docker-compose generation failed"
    exit 1
fi

if [ ! -f "$PROJECT_DIR/docker-compose.generated.yml" ]; then
    echo "Pipeline stopped: docker-compose.generated.yml not found"
    exit 1
fi

# ===============================
# Loop on each service
# ===============================
for SERVICE in "${SERVICES[@]}"; do

    SERVICE_NAME=$(basename "$SERVICE")

    echo "----------------------------------"
    echo "Processing Service: $SERVICE_NAME"
    echo "----------------------------------"

    mkdir -p "$REPORTS_DIR/$SERVICE_NAME"

    #  JUnit (blocking)
    echo "Running JUnit..."
    ./scripts/junit.sh "$SERVICE"
    if [ $? -ne 0 ]; then
        echo "Pipeline stopped: JUnit failed in $SERVICE_NAME"
        exit 1
    fi

    #  Semgrep
    echo "Running Semgrep..."
    REPORTS_DIR="$REPORTS_DIR/$SERVICE_NAME" \
    ./scripts/semgrep.sh "$SERVICE"

    #  Gitleaks
    echo "Running Gitleaks..."
    REPORTS_DIR="$REPORTS_DIR/$SERVICE_NAME" \
    ./scripts/gitleaks.sh "$SERVICE"

done

# ===============================
# Maven Build (all services together)
# ===============================

# Write .mvn/jvm.config so the opens also apply when MAVEN_OPTS is not inherited
_JVM_CONFIG="--add-opens java.base/java.util=ALL-UNNAMED
--add-opens java.base/java.lang=ALL-UNNAMED
--add-opens java.base/java.lang.reflect=ALL-UNNAMED
--add-opens java.base/java.io=ALL-UNNAMED
--add-opens java.base/java.text=ALL-UNNAMED
--add-opens java.base/java.net=ALL-UNNAMED
--add-opens java.base/java.security=ALL-UNNAMED
--add-opens java.base/java.security.cert=ALL-UNNAMED
--add-opens java.base/java.nio=ALL-UNNAMED
--add-opens java.base/sun.nio.ch=ALL-UNNAMED
--add-opens java.base/sun.security.util=ALL-UNNAMED
--add-opens java.desktop/java.awt.font=ALL-UNNAMED
--add-opens java.desktop/java.awt=ALL-UNNAMED
--add-opens java.desktop/java.awt.color=ALL-UNNAMED
--add-opens java.desktop/javax.swing=ALL-UNNAMED
--add-opens java.sql/java.sql=ALL-UNNAMED"
mkdir -p "$PROJECT_DIR/.mvn"
printf '%s\n' "$_JVM_CONFIG" > "$PROJECT_DIR/.mvn/jvm.config"

# ── Patch incompatible plugins in every pom.xml found inside the project ─────
# Use the bundled venv Python — system python3 on Windows hits the Store stub.
_PYTHON="$TOOLS_DIR/.venv/Scripts/python"
"$_PYTHON" - "$PROJECT_DIR" <<'PYEOF'
import os, re, sys

root = sys.argv[1]

WAR_PATTERN = re.compile(
    r'(<artifactId>maven-war-plugin</artifactId>\s*<version>)(2\.\S+?)(</version>)',
    re.DOTALL
)

for dirpath, _, files in os.walk(root):
    for fname in files:
        if fname != 'pom.xml':
            continue
        path = os.path.join(dirpath, fname)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content, n = WAR_PATTERN.subn(r'\g<1>3.4.0\g<3>', content)
        if n:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f'[patch] Upgraded maven-war-plugin 2.x → 3.4.0 in {path}')
PYEOF

echo "Building all services with Maven..."
for SERVICE in "${SERVICES[@]}"; do
    SERVICE_NAME=$(basename "$SERVICE")
    # Also write jvm.config next to each service pom if it differs from the root
    if [ "$SERVICE" != "$PROJECT_DIR" ]; then
        mkdir -p "$SERVICE/.mvn"
        printf '%s\n' "$_JVM_CONFIG" > "$SERVICE/.mvn/jvm.config"
    fi
    echo "Building $SERVICE_NAME..."
    mvn -f "$SERVICE/pom.xml" clean package -DskipTests
    if [ $? -ne 0 ]; then
        echo "Maven build failed for $SERVICE_NAME"
        exit 1
    fi
done

# ── Clean up jvm.config before Docker build ───────────────────────────────
# jvm.config is only needed for the HOST Maven build above.
# If it gets COPY'd into the Docker image, Maven inside the container (which
# may be Java 8 or 11) will fail with "Unrecognized option: --add-opens".
rm -f "$PROJECT_DIR/.mvn/jvm.config"
for SERVICE in "${SERVICES[@]}"; do
    rm -f "$SERVICE/.mvn/jvm.config"
done

# ===============================
# Docker Build (all services together)
# ===============================
echo "Building Docker containers..."
./scripts/docker-build.sh "$PROJECT_DIR"
if [ $? -ne 0 ]; then
    echo "Pipeline stopped: Docker build failed"
    exit 1
fi

# ===============================
# Trivy (scan built image)
# ===============================
echo "Running Trivy scan..."
for SERVICE in "${SERVICES[@]}"; do
    SERVICE_NAME=$(basename "$SERVICE")
    REPORTS_DIR="$REPORTS_DIR/$SERVICE_NAME" \
    ./scripts/trivy.sh "$SERVICE"
done

echo "=================================="
echo " Microservices Pipeline Completed"
echo "=================================="

exit 0