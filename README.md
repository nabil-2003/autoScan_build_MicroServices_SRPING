# 🔐 Secure Pipeline — DevSecOps Automation for Spring Boot Microservices

A fully **self-contained, offline-capable** CI/CD security pipeline for Spring Boot microservice projects.  
All tools are bundled locally — no external CI server (Jenkins, GitHub Actions, etc.) required.

---

## 📑 Table of Contents

1. [Overview](#-overview)
2. [Architecture](#-architecture)
3. [Pipeline Stages](#-pipeline-stages)
4. [Project Structure](#-project-structure)
5. [Prerequisites](#-prerequisites)
6. [Installation](#-installation)
7. [Usage — Web UI](#-usage--web-ui)
8. [Usage — Command Line](#-usage--command-line)
9. [Reports & PDF Export](#-reports--pdf-export)
10. [Tool Versions](#-tool-versions)
11. [Troubleshooting](#-troubleshooting)

---

## 🌐 Overview

**Secure Pipeline** automates the full DevSecOps lifecycle for Java microservice projects:

| Stage | Tool | Purpose |
|-------|------|---------|
| Unit Tests | JUnit (Maven) | Verify correctness |
| SAST | Semgrep | Static code security analysis |
| Secret Detection | Gitleaks | Find hardcoded credentials |
| Build | Apache Maven 3.9.9 + JDK 17 | Compile and package |
| Containerisation | Docker + Docker Compose | Build and run images |
| Image Scanning | Trivy | CVE vulnerability scan on built images |

Everything runs locally — **no cloud account, no internet connection needed after install**.

---

## 🏗 Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    Web UI  (http://localhost:3000)              │
│   ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐    │
│   │ Local    │  │  Git Clone   │  │    ZIP Upload        │    │
│   │ Path     │  │  (URL)       │  │    (.zip file)       │    │
│   └────┬─────┘  └──────┬───────┘  └──────────┬───────────┘    │
│        └───────────────┴──────────────────────┘                │
│                         │                                       │
│              server.js (Express + SSE)                         │
│                         │                                       │
└─────────────────────────┼───────────────────────────────────────┘
                          │  spawns
                          ▼
                    pipeline.sh
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
    junit.sh        semgrep.sh      gitleaks.sh
          │               │               │
          └───────────────┴───────────────┘
                          │
                    Maven build
                          │
                  docker-build.sh
                          │
                    trivy.sh (per service)
                          │
                   reports/<service>/
                    ├── junit/
                    ├── semgrep/
                    ├── gitleaks/
                    └── trivy/
```

### Live Log Streaming

The web server streams every line of `pipeline.sh` output back to the browser in real-time via **Server-Sent Events (SSE)**.  
Errors are automatically classified and surfaced in the PDF report.

---

## 🚦 Pipeline Stages

### 1 — JUnit (blocking)

Runs `mvn test` on each service module.  
If tests **fail**, the pipeline stops immediately — no further stages run.

### 2 — Semgrep (SAST)

Scans Java source code with Semgrep using the `p/java` ruleset.  
Output: `reports/<service>/semgrep/semgrep.json`

### 3 — Gitleaks (Secret Detection)

Scans source files for hardcoded API keys, passwords, tokens, etc.  
Uses a `.gitleaks.toml` allowlist to exclude `reports/`, `target/`, `.mvn/` from scanning.  
Output: `reports/<service>/gitleaks/gitleaks-report.json`

### 4 — Maven Build

Compiles all service modules (`mvn clean package -DskipTests`).  
Uses **local JDK 17 + local Maven 3.9.9** from `tools/`.  
Automatically patches `maven-war-plugin 2.x → 3.4.0` in any `pom.xml` to fix Java 17 reflection issues.

### 5 — Docker Build

Auto-generates a `docker-compose.generated.yml` from the built `.jar` files, then runs:

```
docker compose down --remove-orphans
docker compose -f docker-compose.generated.yml up -d --build
```

### 6 — Trivy (Image Scan)

Scans each built Docker image for known CVEs.  
Output: `reports/<service>/trivy/trivy-report.json`

---

## 📁 Project Structure

```
project/
├── install.sh                   ← One-time installer (run first)
├── pipeline.sh                  ← Main pipeline orchestrator
│
├── scripts/
│   ├── junit.sh                 ← Runs Maven tests
│   ├── semgrep.sh               ← Runs Semgrep SAST
│   ├── gitleaks.sh              ← Runs Gitleaks secret scan
│   ├── docker-build.sh          ← docker compose up
│   ├── trivy.sh                 ← Runs Trivy image scan
│   └── auto-docker-generator.py ← Generates docker-compose.generated.yml
│
├── tools/                       ← All tools installed here (by install.sh)
│   ├── jdk17/                   ← Eclipse Temurin JDK 17
│   ├── maven/                   ← Apache Maven 3.9.9
│   ├── .venv/                   ← Python venv + Semgrep
│   ├── gitleaks.exe             ← Gitleaks binary (Windows)
│   ├── trivy.exe                ← Trivy binary (Windows)
│   └── mvn                      ← Bash wrapper (uses local JDK + Maven)
│
└── web/
    ├── server.js                ← Express.js backend (port 3000)
    ├── package.json
    ├── public/
    │   ├── index.html           ← Web UI
    │   ├── app.js               ← Frontend logic + PDF generation
    │   └── style.css
    ├── uploads/                 ← Temporary ZIP upload storage
    └── workspaces/              ← Cloned/extracted project workspaces
```

---

## ✅ Prerequisites

### Required (must be installed manually)

| Tool | Version | Notes |
|------|---------|-------|
| **Git Bash** | Any recent | Must be at `C:\Program Files\Git\bin\bash.exe` |
| **Node.js** | LTS (18+) | `install.sh` installs via `winget` if missing |
| **Docker Desktop** | Latest | Must be **running** before starting the pipeline |
| **Internet** | First run only | `install.sh` downloads all other tools automatically |

> **Git Bash is required on Windows.** The pipeline scripts are Bash scripts executed via Git Bash's `bash.exe` — not WSL or CMD.

### Automatically installed by `install.sh`

| Tool | Version | Location |
|------|---------|----------|
| Eclipse Temurin JDK 17 | 17 (latest GA) | `tools/jdk17/` |
| Apache Maven | 3.9.9 | `tools/maven/` |
| Python venv + Semgrep | latest | `tools/.venv/` |
| Gitleaks | 8.21.2 | `tools/gitleaks.exe` |
| Trivy | 0.69.0 | `tools/trivy.exe` |
| Node.js dependencies | — | `web/node_modules/` |

---

## 🚀 Installation

### Step 1 — Open Git Bash in the project folder

```bash
cd /c/Users/YourName/Desktop/scripts/project
```

### Step 2 — Run the installer

```bash
bash install.sh
```

The installer will:

- Download and extract JDK 17, Maven 3.9.9, Gitleaks, Trivy into `tools/`
- Create a Python virtual environment at `tools/.venv/` and install Semgrep
- Install Node.js via `winget` if not found
- Run `npm install` in `web/`
- Warn if Docker Desktop is not detected

Expected output:

```
  ╔══════════════════════════════════════════════╗
  ║     🔐 Secure Pipeline — Install Script      ║
  ╚══════════════════════════════════════════════╝

── 1/6  Java Development Kit 17 ──
  ✔  JDK 17 installed → tools/jdk17
── 2/6  Apache Maven ─────────────
  ✔  Maven 3.9.9 installed → tools/maven
── 3/6  Python & Semgrep ─────────
  ✔  Semgrep installed → tools/.venv
── 4/6  Gitleaks ─────────────────
  ✔  Gitleaks installed → tools/gitleaks.exe
── 5/6  Trivy ────────────────────
  ✔  Trivy installed → tools/trivy.exe
── 6/6  Node.js (web server) ─────
  ✔  Node.js already available: v20.x.x
  ✔  Web server dependencies installed → web/node_modules
  ✔  Docker available: Docker version 27.x.x

  ╔══════════════════════════════════════════════╗
  ║          ✅  Installation Complete!           ║
  ╚══════════════════════════════════════════════╝
```

### Step 3 — Start the web server

```bash
node web/server.js
```

Open **http://localhost:3000** in your browser.

---

## 🖥 Usage — Web UI

Open `http://localhost:3000` in your browser.

### Input Modes

#### Option A — Local Path

Paste the absolute path to an existing Spring Boot project on your machine:

```
C:\Users\YourName\Desktop\micros-main
```

#### Option B — Git Clone

Paste a Git repository URL. The server clones it into `web/workspaces/<uuid>/` and runs the pipeline:

```
https://github.com/example/spring-boot-microservices.git
```

#### Option C — ZIP Upload

Upload a `.zip` file containing the Spring Boot project. The server extracts it and runs the pipeline.

### Running the Pipeline

1. Choose an input mode and fill in the path / URL / file
2. Click **Run Pipeline**
3. Watch the **live log stream** in real time (colour-coded output)
4. When complete, the **Report Card** appears showing pass/fail for each stage × service
5. Click **Download PDF Report** to export the full report

### Report Card Statuses

| Icon | Meaning |
|------|---------|
| ✅ Pass | Stage completed without errors |
| ❌ Fail | Stage encountered errors (see PDF for details) |
| ⏭ Skipped | No report file found for this stage |

---

## 💻 Usage — Command Line

Run the pipeline directly without the web UI:

```bash
cd /c/Users/YourName/Desktop/scripts/project
bash pipeline.sh /c/Users/YourName/Desktop/micros-main
```

> Use **forward slashes** and the full absolute path when running in Git Bash.

### Pipeline Flow

1. Reads `pom.xml` to detect multi-module vs single-module layout
2. Per-service loop: JUnit → Semgrep → Gitleaks
3. Maven build for all modules
4. Docker Compose generation + container startup
5. Trivy image scan per service
6. Reports written to `<project>/reports/<service>/`

### Report Directory Layout

```
micros-main/
└── reports/
    ├── common/
    │   ├── junit/        ← Maven Surefire XML
    │   ├── semgrep/      ← semgrep.json
    │   ├── gitleaks/     ← gitleaks-report.json
    │   └── trivy/        ← trivy-report.json
    ├── eureka-server/
    ├── gateway/
    └── product/
```

---

## 📄 Reports & PDF Export

### PDF Report Contents

| Section | Description |
|---------|-------------|
| Pipeline Summary | Project name, date, total stages, pass/fail counts |
| Results Table | One row per service × stage with ✅/❌/⏭ status |
| Pipeline Errors | Significant errors only (red table) — only present if errors occurred |

### Error Filtering

The PDF only captures genuine failures — progress noise is excluded:

**Included in errors:** `[ERROR]`, `BUILD FAILURE`, `Exception`, `Error:`, `fatal:`, `FAILED`, `cannot find`, `not found`  
**Excluded:** Semgrep progress banners, Trivy INFO lines, Docker Compose status messages, `JAVA_TOOL_OPTIONS` notices

---

## 🔧 Tool Versions

| Tool | Version | Source |
|------|---------|--------|
| Eclipse Temurin JDK 17 | 17 GA (latest) | adoptium.net |
| Apache Maven | 3.9.9 | archive.apache.org |
| Semgrep | latest stable | PyPI |
| Gitleaks | 8.21.2 | github.com/gitleaks/gitleaks |
| Trivy | 0.69.0 | github.com/aquasecurity/trivy |
| Express.js | ^4.18.2 | npm |
| jsPDF | 2.5.1 | CDN |
| jspdf-autotable | 3.8.2 | CDN |

To update a tool version, change its version variable in `install.sh` and re-run it.

---

## 🛠 Troubleshooting

### Windows Store opens or `python: command not found`

The pipeline uses `tools/.venv/Scripts/python` — never system Python.  
Re-run `bash install.sh` to recreate the venv.

### Gitleaks hangs or scans the entire drive

Make sure `pipeline.sh` is called with the project **path as an argument**.  
The scan target is `$1`, not the current directory:

```bash
bash pipeline.sh /c/path/to/your/project   # correct
bash pipeline.sh                           # wrong — will scan everything
```

### Maven fails with `InaccessibleObjectException`

Java 17 module-access issue. The pipeline sets all required `--add-opens` in `MAVEN_OPTS` automatically.  
Verify `JAVA_HOME` is printed as `tools/jdk17` at pipeline startup.

### Maven fails inside Docker with `Unrecognized option: --add-opens`

The pipeline deletes `.mvn/jvm.config` before the Docker build step.  
If the file was re-created manually, remove it:

```bash
rm -f /c/path/to/project/.mvn/jvm.config
```

### Trivy shows ⏭ Skipped for all services

Docker images were not built successfully.  
Confirm Docker Desktop is running and the Maven build passed before Trivy runs.

### Report Card shows 0 services / 0 stage results

Look in the live log for:

```
📊 Report built: N service(s), N stage result(s) found
```

If `N = 0`, the pipeline did not write reports to `<workspace>/reports/`. Check that the pipeline ran to at least stage 2.

### Port 3000 already in use

```cmd
netstat -ano | findstr :3000
taskkill /PID <PID> /F
```

### Private Git repository

Include a Personal Access Token in the URL:

```
https://<token>@github.com/yourname/repo.git
```

### `docker compose` command not found

Upgrade to **Docker Desktop 4.x+** which bundles Compose V2 as a built-in plugin.

---

## ⚡ Quick Reference

```bash
# First-time setup (run once)
bash install.sh

# Start the web UI
node web/server.js
# → Open http://localhost:3000

# Run pipeline directly from CLI
bash pipeline.sh /c/path/to/your/spring-boot-project

# Update / reinstall a tool (edit version in install.sh first)
bash install.sh
```

---

## 🔒 Security Notes

- All tools run **locally** — no data is sent to any external service
- Semgrep runs in **offline mode** — no Semgrep account required
- Trivy downloads its vulnerability database on first run; subsequent runs use a local cache
- Gitleaks file size is capped at **5 MB per file** to prevent hangs on large binaries

---

*Secure Pipeline v1.0 — DevSecOps automation for Spring Boot microservices.*
