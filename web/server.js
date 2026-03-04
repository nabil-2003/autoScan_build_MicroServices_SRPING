const express = require('express');
const multer  = require('multer');
const { v4: uuidv4 } = require('uuid');
const simpleGit = require('simple-git');
const unzipper  = require('unzipper');
const path  = require('path');
const fs    = require('fs');
const { spawn } = require('child_process');

const app  = express();
const PORT = 3000;

// ── Paths ────────────────────────────────────────────────────────────────────
const PROJECT_ROOT = path.resolve(__dirname, '..');          // …/project
const WORKSPACES   = path.join(__dirname, 'workspaces');     // …/web/workspaces
const UPLOADS_DIR  = path.join(__dirname, 'uploads');        // …/web/uploads
const PIPELINE_SH  = path.join(PROJECT_ROOT, 'pipeline.sh');
fs.mkdirSync(WORKSPACES, { recursive: true });
fs.mkdirSync(UPLOADS_DIR, { recursive: true });

// ── Startup sanity-check ────────────────────────────────────────────────────
console.log('  📁  PROJECT_ROOT :', PROJECT_ROOT);
console.log('  📜  pipeline.sh  :', PIPELINE_SH, fs.existsSync(PIPELINE_SH) ? '✔' : '✘ NOT FOUND');

// ── SSE store  { id → { res, done } } ────────────────────────────────────────
const clients = {};

// ── Multer (zip upload) ──────────────────────────────────────────────────────
const upload = multer({
  dest: path.join(__dirname, 'uploads'),
  fileFilter: (_req, file, cb) =>
    cb(null, file.originalname.toLowerCase().endsWith('.zip')),
});
fs.mkdirSync(path.join(__dirname, 'uploads'), { recursive: true });

// ── Static files ─────────────────────────────────────────────────────────────
app.use(express.static(path.join(__dirname, 'public')));
app.use(express.json());

// ── SSE: subscribe to a job ──────────────────────────────────────────────────
app.get('/logs/:id', (req, res) => {
  const { id } = req.params;
  res.setHeader('Content-Type',  'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection',    'keep-alive');
  res.flushHeaders();

  clients[id] = res;

  req.on('close', () => {
    delete clients[id];
  });
});

// ── Helper: push SSE line ────────────────────────────────────────────────────
function push(id, type, data) {
  const res = clients[id];
  if (!res) return;
  res.write(`data: ${JSON.stringify({ type, data })}\n\n`);
}

function finish(id, success) {
  push(id, 'done', { success });
  const res = clients[id];
  if (res) res.end();
  delete clients[id];
}

// ── Resolve Git Bash executable (never WSL) ──────────────────────────────────
function findGitBash() {
  const candidates = [
    'C:\\Program Files\\Git\\bin\\bash.exe',
    'C:\\Program Files\\Git\\usr\\bin\\bash.exe',
    'C:\\Program Files (x86)\\Git\\bin\\bash.exe',
    'C:\\Program Files (x86)\\Git\\usr\\bin\\bash.exe',
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  // Last resort: hope it's on PATH as git-bash; will throw a clear error if not
  return 'bash';
}
const GIT_BASH = findGitBash();

// ── Build structured report – search recursively under workspace ──────────────
function findUnder(dir, name, maxDepth = 6, depth = 0) {
  if (depth > maxDepth) return null;
  const target = path.join(dir, name);
  if (fs.existsSync(target)) return target;
  try {
    for (const entry of fs.readdirSync(dir)) {
      if (entry.startsWith('.')) continue;
      const full = path.join(dir, entry);
      try {
        if (fs.statSync(full).isDirectory()) {
          const found = findUnder(full, name, maxDepth, depth + 1);
          if (found) return found;
        }
      } catch (_) {}
    }
  } catch (_) {}
  return null;
}

function buildReport(workspaceDir) {
  const result = { services: {}, docker: false, timestamp: new Date().toISOString() };

  // docker-compose file can be at any depth (single or multi-module)
  result.docker = !!findUnder(workspaceDir, 'docker-compose.generated.yml');

  // reports/ is always written directly under projectDir by pipeline.sh:
  //   REPORTS_DIR="$PROJECT_DIR/reports/$SERVICE_NAME"
  // Use a direct path rather than a recursive search to avoid landing on
  // a stale or wrong reports/ directory in a parent/sibling folder.
  const reportsDir = path.join(workspaceDir, 'reports');
  if (!fs.existsSync(reportsDir)) {
    // fallback: project might be nested one level deep (e.g. uploads/<id>/<repo>)
    // try one level up then one level down
    const found = findUnder(workspaceDir, 'reports');
    if (!found) return result;
    return _collectServices(result, found);
  }
  return _collectServices(result, reportsDir);
}

function _collectServices(result, reportsDir) {
  try {
    for (const svcName of fs.readdirSync(reportsDir)) {
      const svcDir = path.join(reportsDir, svcName);
      try { if (!fs.statSync(svcDir).isDirectory()) continue; } catch (_) { continue; }
      result.services[svcName] = {};
      for (const stage of ['semgrep', 'gitleaks', 'trivy']) {
        const rf = path.join(svcDir, stage, 'result.json');
        if (fs.existsSync(rf)) {
          try { result.services[svcName][stage] = JSON.parse(fs.readFileSync(rf, 'utf8')); }
          catch (_) {}
        }
      }
    }
  } catch (_) {}
  return result;
}

// ── Run the pipeline against a workspace dir ─────────────────────────────────
function runPipeline(id, projectDir) {
  // Guard: make sure pipeline.sh actually exists
  if (!fs.existsSync(PIPELINE_SH)) {
    push(id, 'err', `pipeline.sh not found at: ${PIPELINE_SH}`);
    finish(id, false);
    return;
  }

  // Use a path relative to PROJECT_ROOT (forward slashes) so Git Bash receives
  // a plain relative path with no Windows drive-letter conversion needed.
  const relDir = path.relative(PROJECT_ROOT, projectDir).replace(/\\/g, '/');

  push(id, 'log', `▶  Starting pipeline for: ${projectDir}`);
  push(id, 'log', `🔧  Bash   : ${GIT_BASH}`);
  push(id, 'log', `⚙️   Command: bash pipeline.sh ${relDir}`);

  const proc = spawn(GIT_BASH, ['./pipeline.sh', relDir], {
    cwd: PROJECT_ROOT,          // run from project root – all relative paths resolve here
    env: {
      ...process.env,
      PYTHONUTF8: '1',          // fix UnicodeEncodeError for emoji on Windows
      PYTHONIOENCODING: 'utf-8',
    },
    shell: false,
  });

  proc.stdout.on('data', (d) => {
    d.toString().split('\n').filter(Boolean).forEach((line) => {
      push(id, 'log', line);
    });
  });

  proc.stderr.on('data', (d) => {
    d.toString().split('\n').filter(Boolean).forEach((line) => {
      push(id, 'err', line);
    });
  });

  proc.on('close', (code) => {
    push(id, 'log', `\n──────────────────────────────────────`);
    if (code === 0) {
      push(id, 'log', ' Pipeline completed successfully');
    } else {
      push(id, 'err', ` Pipeline exited with code ${code}`);
    }
    // Collect report JSON files and push structured report event
    const report = buildReport(projectDir);
    const svcCount  = Object.keys(report.services).length;
    const stageCount = Object.values(report.services)
      .reduce((n, s) => n + Object.keys(s).length, 0);
    push(id, 'log', `📊  Report built: ${svcCount} service(s), ${stageCount} stage result(s) found`);
    push(id, 'report', report);
    finish(id, code === 0);

    // Clean up workspace after 10 min
    setTimeout(() => {
      fs.rmSync(projectDir, { recursive: true, force: true });
    }, 10 * 60 * 1000);
  });

  proc.on('error', (err) => {
    push(id, 'err', `Failed to start pipeline: ${err.message}`);
    finish(id, false);
  });
}

// ── POST /run/local  { path } ──────────────────────────────────────────────────
app.post('/run/local', (req, res) => {
  const { path: localPath } = req.body;
  if (!localPath) return res.status(400).json({ error: 'path required' });

  // Resolve relative to PROJECT_ROOT (same as running `bash pipeline.sh <path>`)
  const resolvedPath = path.resolve(PROJECT_ROOT, localPath.replace(/\\/g, '/'));

  if (!fs.existsSync(resolvedPath)) {
    return res.status(400).json({ error: `Path not found: ${resolvedPath}` });
  }

  const id = uuidv4();
  res.json({ id });

  setTimeout(() => {
    push(id, 'log', `📁  Local path: ${resolvedPath}`);
    runPipeline(id, resolvedPath);
  }, 600);
});

// ── POST /run/git  { url } ───────────────────────────────────────────────────
app.post('/run/git', async (req, res) => {
  const { url } = req.body;
  if (!url) return res.status(400).json({ error: 'url required' });

  const id       = uuidv4();
  const repoName = path.basename(url, '.git') || id;
  const dir      = path.join(UPLOADS_DIR, id, repoName);   // ← lands in uploads/
  res.json({ id });

  // Clone asynchronously so SSE stream is already open when logs arrive
  setTimeout(async () => {
    push(id, 'log', `🔗  Cloning ${url} into uploads/${id}/${repoName} …`);
    try {
      fs.mkdirSync(path.dirname(dir), { recursive: true });
      await simpleGit().clone(url, dir, ['--depth', '1']);
      push(id, 'log', `✔  Clone complete → uploads/${id}/${repoName}`);
      runPipeline(id, dir);
    } catch (err) {
      push(id, 'err', `Clone failed: ${err.message}`);
      finish(id, false);
    }
  }, 600); // small delay so browser can open SSE first
});

// ── POST /run/upload  (multipart zip) ────────────────────────────────────────
app.post('/run/upload', upload.single('project'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No zip file received' });

  const id  = uuidv4();
  const dir = path.join(UPLOADS_DIR, id);   // ← extract into uploads/<id>/
  res.json({ id });

  setTimeout(async () => {
    push(id, 'log', `📦  Extracting ${req.file.originalname} into uploads/${id}/ …`);
    try {
      fs.mkdirSync(dir, { recursive: true });

      // ── Use unzipper.Open.file() — reliably waits for all entries to flush ──
      const zipPath = req.file.path;
      const directory = await unzipper.Open.file(zipPath);
      await directory.extract({ path: dir });
      fs.rmSync(zipPath, { force: true });   // delete raw multer temp file

      // The zip usually produces a single top-level folder (e.g. micros-main/).
      // Pass that directly to pipeline.sh — it receives the real project dir.
      const entries = fs.readdirSync(dir).filter(e => {
        try { return fs.statSync(path.join(dir, e)).isDirectory(); } catch (_) { return false; }
      });
      const projectDir = entries.length === 1 ? path.join(dir, entries[0]) : dir;

      push(id, 'log', `✔  Extraction complete → uploads/${id}/${path.basename(projectDir)}`);
      runPipeline(id, projectDir);
    } catch (err) {
      push(id, 'err', `Extraction failed: ${err.message}`);
      finish(id, false);
    }
  }, 600);
});

// ── Start ─────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`\n  🚀  Pipeline UI  →  http://localhost:${PORT}\n`);
});
