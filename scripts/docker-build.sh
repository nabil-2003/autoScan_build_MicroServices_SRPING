#!/bin/bash

APP_DIR=${1:-.}

echo "=================================="
echo " Building Docker Compose Project"
echo "=================================="

cd "$APP_DIR" || exit 1

# Vérifier que le fichier existe
if [ ! -f docker-compose.generated.yml ]; then
    echo "Error: docker-compose.generated.yml not found!"
    exit 1
fi

echo "Running docker compose build..."

# ── Tear down any existing containers from a previous run ──────────────────
echo "Stopping and removing any existing containers..."
docker compose -f docker-compose.generated.yml down --remove-orphans 2>/dev/null || true

docker compose -f docker-compose.generated.yml up -d --build

if [ $? -ne 0 ]; then
    echo "Docker build failed."
    exit 1
fi

echo "Docker containers started successfully."
exit 0