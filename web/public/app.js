/* ── DOM refs ──────────────────────────────────────────────── */
const inputCard    = document.getElementById('input-card');
const progressCard = document.getElementById('progress-card');
const reportCard   = document.getElementById('report-card');
const logOutput    = document.getElementById('log-output');
const statusBadge  = document.getElementById('status-badge');
const stepsList    = document.getElementById('steps-list');
const btnReset     = document.getElementById('btn-reset');
const btnClear     = document.getElementById('btn-clear');
const btnPdf       = document.getElementById('btn-pdf');

/* ── Last report data (for PDF) ────────────────────────────── */
let lastReport = null;

// Tabs
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
  });
});

/* ── Dropzone ──────────────────────────────────────────────── */
const dropzone  = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const dzName    = document.getElementById('dz-filename');
const btnZip    = document.getElementById('btn-zip');
let selectedFile = null;

function setFile(file) {
  if (!file || !file.name.endsWith('.zip')) return;
  selectedFile = file;
  dzName.textContent = `📄 ${file.name}`;
  dzName.classList.remove('hidden');
  dropzone.classList.add('has-file');
  btnZip.disabled = false;
}

dropzone.addEventListener('click', (e) => {
  if (e.target !== fileInput) fileInput.click();
});
dropzone.addEventListener('dragover',  (e) => { e.preventDefault(); dropzone.classList.add('over'); });
dropzone.addEventListener('dragleave', ()  => { dropzone.classList.remove('over'); });
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('over');
  setFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => setFile(fileInput.files[0]));

/* ── Pipeline Step Tracker ─────────────────────────────────── */
const STEP_KEYS = {
  'Clone': 'clone', 'Cloning': 'clone', 'Clone complete': 'clone', 'Extraction complete': 'clone',
  'docker-compose': 'compose', 'Generating docker': 'compose',
  'JUnit': 'junit', 'Running JUnit': 'junit',
  'Semgrep': 'semgrep', 'Running Semgrep': 'semgrep',
  'Gitleaks': 'leaks', 'Running Gitleaks': 'leaks',
  'Maven': 'maven', 'Building': 'maven',
  'Docker': 'docker', 'Building Docker': 'docker',
  'Trivy': 'trivy', 'Running Trivy': 'trivy',
};

let activeStep = null;

function detectStep(line) {
  for (const [keyword, key] of Object.entries(STEP_KEYS)) {
    if (line.includes(keyword)) return key;
  }
  return null;
}

function markStep(key, state) {
  const el = stepsList.querySelector(`[data-key="${key}"]`);
  if (!el) return;
  el.classList.remove('active', 'done', 'failed');
  el.classList.add(state);
}

function setStepActive(key) {
  if (activeStep && activeStep !== key) markStep(activeStep, 'done');
  if (key) { markStep(key, 'active'); activeStep = key; }
}

function finishAllSteps(success) {
  if (activeStep) markStep(activeStep, success ? 'done' : 'failed');
}

/* ── Error significance filter ─────────────────────────────── */
// Many tools (Semgrep, Trivy, Maven, Gitleaks) write progress/info/help to
// stderr. Only lines that signal a genuine failure belong in the PDF report.
function isSignificantError(text) {
  if (!text || !text.trim()) return false;
  // ── Noise exclusions ──────────────────────────────────────
  // Maven JVM notice
  if (text.includes('Picked up JAVA_TOOL_OPTIONS')) return false;
  // Trivy / tool timestamped INFO or WARN lines
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+(INFO|WARN|DEBUG)/.test(text)) return false;
  // Semgrep banners, progress counters, summary lines
  if (/^[\s%]*(%{3,}|Scan(ning|ned|completed|skipped|Status|Summary)|Ran \d|Rules run|Targets scanned|Parsed lines|findings|semgrep login|false.negatives)/i.test(text)) return false;
  // Gitleaks / generic CLI help/usage text (flag listings)
  if (/^\s*(Flags:|Usage:|Global Flags:)/i.test(text)) return false;
  if (/^\s+(-[a-zA-Z],?\s+)?--[a-zA-Z]/.test(text)) return false;
  // Docker Compose status lines (Container/Network Creating/Started/…)
  if (/\b(Container|Network)\b.+\b(Creating|Created|Starting|Started|Waiting|Healthy|Built)\b/.test(text)) return false;

  // ── Real error signals ────────────────────────────────────
  return /\[ERROR\]/.test(text)
      || /^error:/i.test(text.trimStart())
      || /\bException\b/.test(text)
      || /BUILD FAILURE/.test(text)
      || /\bFAILED\b/.test(text)
      || /\bfatal\b/i.test(text)
      || /unknown flag/i.test(text)
      || /cannot find|not found|no such file/i.test(text);
}

/* ── Log rendering ─────────────────────────────────────────── */
function appendLog(text, type) {
  const span = document.createElement('span');
  if (type === 'err') {
    span.className = 'line-err';
    // Only collect lines that are genuine errors (not progress/info/help noise)
    if (isSignificantError(text)) {
      pipelineErrors.push({ stage: activeStep || 'general', message: text });
    }
  } else if (text.includes('✅') || text.includes('✔')) {
    span.className = 'line-ok';
  }
  span.textContent = text + '\n';
  logOutput.appendChild(span);
  logOutput.scrollTop = logOutput.scrollHeight;

  const step = detectStep(text);
  if (step) setStepActive(step);
}

btnClear.addEventListener('click', () => { logOutput.innerHTML = ''; });

/* ── Start pipeline ────────────────────────────────────────── */
function startPipeline(id) {
  inputCard.classList.add('hidden');
  progressCard.classList.remove('hidden');
  btnReset.classList.add('hidden');
  logOutput.innerHTML = '';
  statusBadge.className = 'badge running';
  statusBadge.textContent = 'Running…';
  activeStep = null;
  pipelineErrors = [];
  stepsList.querySelectorAll('.step').forEach(s => s.classList.remove('active','done','failed'));

  const es = new EventSource(`/logs/${id}`);

  es.onmessage = (e) => {
    const { type, data } = JSON.parse(e.data);

    if (type === 'log') { appendLog(data, 'log'); }
    else if (type === 'err') { appendLog(data, 'err'); }
    else if (type === 'report') {
      // Attach collected errors to the report before storing/rendering
      data.errors = pipelineErrors.slice();
      lastReport = data;
      renderReport(data);
    }
    else if (type === 'done') {
      // If pipeline stopped before a report event (e.g. early failure), still save errors
      if (!lastReport) lastReport = { services: {}, docker: false, errors: pipelineErrors.slice(), timestamp: new Date().toISOString() };
      else lastReport.errors = pipelineErrors.slice();
      es.close();
      finishAllSteps(data.success);
      statusBadge.className = 'badge ' + (data.success ? 'success' : 'failure');
      statusBadge.textContent = data.success ? 'Success ✅' : 'Failed ❌';
      btnReset.classList.remove('hidden');
    }
  };

  es.onerror = () => {
    es.close();
    appendLog('SSE connection closed.', 'err');
    btnReset.classList.remove('hidden');
  };
}

/* ── Git button ────────────────────────────────────────────── */
document.getElementById('btn-git').addEventListener('click', async () => {
  const url = document.getElementById('git-url').value.trim();
  if (!url) { alert('Please enter a Git URL'); return; }

  const res  = await fetch('/run/git', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  const { id, error } = await res.json();
  if (error) { alert('Error: ' + error); return; }
  startPipeline(id);
});

/* ── ZIP button ────────────────────────────────────────────── */
btnZip.addEventListener('click', async () => {
  if (!selectedFile) return;
  const form = new FormData();
  form.append('project', selectedFile);

  const res  = await fetch('/run/upload', { method: 'POST', body: form });
  const { id, error } = await res.json();
  if (error) { alert('Error: ' + error); return; }
  startPipeline(id);
});

/* ── Report renderer ───────────────────────────────────────── */
const STAGE_LABELS = { semgrep: 'Semgrep', gitleaks: 'Gitleaks', trivy: 'Trivy' };

function renderReport(report) {
  if (!report) return;
  reportCard.classList.remove('hidden');

  // Docker compose row
  const dockerEl = document.getElementById('report-docker');
  dockerEl.className = 'report-docker-row';
  dockerEl.innerHTML = report.docker
    ? `<span class="rep-pill ok">✅ docker-compose.generated.yml created</span>`
    : `<span class="rep-pill warn">⚠️ docker-compose.generated.yml not found</span>`;

  // Services
  const body = document.getElementById('report-body');
  body.innerHTML = '';
  const services = report.services || {};
  let allOk = report.docker;

  for (const [svcName, stages] of Object.entries(services)) {
    const sec = document.createElement('div');
    sec.className = 'rep-service';
    sec.innerHTML = `<div class="rep-service-name">📦 ${svcName}</div>`;

    const grid = document.createElement('div');
    grid.className = 'rep-grid';

    for (const [stageKey, label] of Object.entries(STAGE_LABELS)) {
      const cell = document.createElement('div');
      cell.className = 'rep-cell';
      const d = stages[stageKey];
      if (d) {
        const ok = d.status === 'SUCCESS';
        if (!ok) allOk = false;
        cell.innerHTML =
          `<span class="rep-stage">${label}</span>` +
          `<span class="rep-status ${ok ? 'ok' : 'fail'}">${ok ? '✅' : '❌'} ${d.status}</span>` +
          `<span class="rep-msg">${d.message}</span>` +
          `<span class="rep-dur">${d.duration_ms} ms</span>`;
      } else {
        cell.innerHTML =
          `<span class="rep-stage">${label}</span>` +
          `<span class="rep-status skip">⏭ Skipped</span>`;
      }
      grid.appendChild(cell);
    }
    sec.appendChild(grid);
    body.appendChild(sec);
  }

  if (!Object.keys(services).length) {
    body.innerHTML = '<p class="rep-empty">No scan reports found — tools may not be installed.</p>';
  }

  const overall = document.getElementById('report-overall');
  overall.className = 'badge ' + (allOk ? 'success' : 'failure');
  overall.textContent = allOk ? 'All Passed ✅' : 'Issues Found ❌';
}

/* ── Local Path button ────────────────────────────────────────── */
document.getElementById('btn-local').addEventListener('click', async () => {
  const localPath = document.getElementById('local-path').value.trim();
  if (!localPath) { alert('Please enter a project path'); return; }

  const res = await fetch('/run/local', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: localPath }),
  });
  const { id, error } = await res.json();
  if (error) { alert('Error: ' + error); return; }
  startPipeline(id);
});

/* ── PDF Report ────────────────────────────────────────────── */
btnPdf.addEventListener('click', () => generatePDF());

function generatePDF() {
  if (!lastReport) return;
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });

  const PAGE_W = 210;
  const MARGIN = 15;
  const COL_W  = PAGE_W - MARGIN * 2;
  let y = MARGIN;

  const ts = lastReport.timestamp
    ? new Date(lastReport.timestamp).toLocaleString()
    : new Date().toLocaleString();

  // ── Compute overall pass/fail ──────────────────────────────
  const services = lastReport.services || {};
  let totalStages = 0, passedStages = 0;
  for (const stages of Object.values(services)) {
    for (const d of Object.values(stages)) {
      totalStages++;
      if (d.status === 'SUCCESS') passedStages++;
    }
  }
  const allOk = lastReport.docker && passedStages === totalStages;

  // ── Header banner ──────────────────────────────────────────
  doc.setFillColor(22, 27, 34);          // --surface
  doc.rect(0, 0, PAGE_W, 28, 'F');
  doc.setTextColor(88, 166, 255);        // --accent
  doc.setFontSize(16);
  doc.setFont('helvetica', 'bold');
  doc.text('\uD83D\uDD10 Secure Spring Boot Pipeline Report', MARGIN, 12);
  doc.setFontSize(8);
  doc.setTextColor(139, 148, 158);       // --muted
  doc.text(`Generated: ${ts}`, MARGIN, 20);
  y = 36;

  // ── Overall status ─────────────────────────────────────────
  doc.setFontSize(11);
  doc.setFont('helvetica', 'bold');
  doc.setTextColor(allOk ? 63 : 248, allOk ? 185 : 81, allOk ? 80 : 73);
  doc.text(`Overall Status: ${allOk ? '✅  ALL PASSED' : '❌  ISSUES FOUND'}`, MARGIN, y);
  y += 8;

  // ── Docker compose ─────────────────────────────────────────
  doc.setFontSize(9);
  doc.setFont('helvetica', 'normal');
  if (lastReport.docker) {
    doc.setTextColor(63, 185, 80);
    doc.text('✅  docker-compose.generated.yml created', MARGIN, y);
  } else {
    doc.setTextColor(210, 153, 34);
    doc.text('⚠️  docker-compose.generated.yml NOT found', MARGIN, y);
  }
  y += 10;

  // ── Summary table (all services × all stages) ───────────────
  const STAGE_KEYS = ['semgrep', 'gitleaks', 'trivy'];
  const STAGE_LABELS_PDF = { semgrep: 'Semgrep', gitleaks: 'Gitleaks', trivy: 'Trivy' };

  const summaryHead = [['Service', 'Semgrep', 'Gitleaks', 'Trivy']];
  const summaryBody = Object.entries(services).map(([name, stages]) => [
    name,
    ...STAGE_KEYS.map(k => stages[k] ? (stages[k].status === 'SUCCESS' ? '✅ PASS' : '❌ FAIL') : '⏭ Skip'),
  ]);

  doc.setFontSize(10);
  doc.setFont('helvetica', 'bold');
  doc.setTextColor(230, 237, 243);
  doc.text('Summary', MARGIN, y);
  y += 4;

  doc.autoTable({
    startY: y,
    head: summaryHead,
    body: summaryBody,
    margin: { left: MARGIN, right: MARGIN },
    styles: { fontSize: 9, cellPadding: 3, fillColor: [13, 17, 23], textColor: [230, 237, 243], lineColor: [48, 54, 61], lineWidth: 0.2 },
    headStyles: { fillColor: [22, 27, 34], textColor: [88, 166, 255], fontStyle: 'bold' },
    alternateRowStyles: { fillColor: [22, 27, 34] },
    didParseCell: (data) => {
      if (data.section === 'body' && data.column.index > 0) {
        const v = data.cell.raw;
        if (v.includes('PASS')) data.cell.styles.textColor = [63, 185, 80];
        else if (v.includes('FAIL')) data.cell.styles.textColor = [248, 81, 73];
        else data.cell.styles.textColor = [139, 148, 158];
      }
    },
  });
  y = doc.lastAutoTable.finalY + 10;

  // ── Per-service detail tables ───────────────────────────────
  for (const [svcName, stages] of Object.entries(services)) {
    if (y > 250) { doc.addPage(); y = MARGIN; }

    doc.setFontSize(10);
    doc.setFont('helvetica', 'bold');
    doc.setTextColor(88, 166, 255);
    doc.text(`Service: ${svcName}`, MARGIN, y);
    y += 4;

    const rows = STAGE_KEYS
      .filter(k => stages[k])
      .map(k => {
        const d = stages[k];
        const ok = d.status === 'SUCCESS';
        return [
          STAGE_LABELS_PDF[k],
          ok ? '✅ PASS' : '❌ FAIL',
          d.message || '',
          d.duration_ms != null ? `${d.duration_ms} ms` : '',
        ];
      });

    if (!rows.length) {
      doc.setFontSize(8); doc.setTextColor(139, 148, 158); doc.setFont('helvetica', 'normal');
      doc.text('No scan results available.', MARGIN, y + 4);
      y += 12;
      continue;
    }

    doc.autoTable({
      startY: y,
      head: [['Stage', 'Status', 'Message', 'Duration']],
      body: rows,
      margin: { left: MARGIN, right: MARGIN },
      columnStyles: { 0: { cellWidth: 25 }, 1: { cellWidth: 24 }, 2: { cellWidth: 105 }, 3: { cellWidth: 26 } },
      styles: { fontSize: 8, cellPadding: 3, fillColor: [13, 17, 23], textColor: [230, 237, 243], lineColor: [48, 54, 61], lineWidth: 0.2, overflow: 'linebreak' },
      headStyles: { fillColor: [22, 27, 34], textColor: [88, 166, 255], fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [22, 27, 34] },
      didParseCell: (data) => {
        if (data.section === 'body' && data.column.index === 1) {
          const v = data.cell.raw;
          if (v.includes('PASS')) data.cell.styles.textColor = [63, 185, 80];
          else if (v.includes('FAIL')) data.cell.styles.textColor = [248, 81, 73];
        }
      },
    });
    y = doc.lastAutoTable.finalY + 8;
  }

  // ── Pipeline Errors ───────────────────────────────────────
  if (lastReport.errors && lastReport.errors.length > 0) {
    if (y > 220) { doc.addPage(); y = 20; }

    doc.setFontSize(12);
    doc.setFont('helvetica', 'bold');
    doc.setTextColor(248, 81, 73);
    doc.text('Pipeline Errors', MARGIN, y);
    y += 6;

    // thin red rule
    doc.setDrawColor(248, 81, 73);
    doc.setLineWidth(0.4);
    doc.line(MARGIN, y, PAGE_W - MARGIN, y);
    y += 4;

    const errorRows = lastReport.errors.map(e => [
      (STAGE_LABELS_PDF[e.stage] || e.stage || 'General'),
      e.message || '',
    ]);

    doc.autoTable({
      startY: y,
      head: [['Stage', 'Error Message']],
      body: errorRows,
      margin: { left: MARGIN, right: MARGIN },
      columnStyles: { 0: { cellWidth: 35 }, 1: { cellWidth: 145 } },
      styles: { fontSize: 8, cellPadding: 3, fillColor: [13, 17, 23], textColor: [230, 237, 243], lineColor: [48, 54, 61], lineWidth: 0.2, overflow: 'linebreak' },
      headStyles: { fillColor: [80, 20, 20], textColor: [248, 81, 73], fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [22, 10, 10] },
      didParseCell: (data) => {
        if (data.section === 'body') {
          data.cell.styles.textColor = [248, 81, 73];
        }
      },
    });
    y = doc.lastAutoTable.finalY + 8;
  }

  // ── Footer on every page ───────────────────────────────────
  const totalPages = doc.internal.getNumberOfPages();
  for (let p = 1; p <= totalPages; p++) {
    doc.setPage(p);
    doc.setFontSize(7);
    doc.setTextColor(139, 148, 158);
    doc.text(`Page ${p} / ${totalPages}  —  Secure Spring Boot Pipeline`, MARGIN, 292);
  }

  doc.save(`pipeline-report-${new Date().toISOString().slice(0,19).replace(/[T:]/g,'-')}.pdf`);
}

/* ── Reset ─────────────────────────────────────────────────── */
btnReset.addEventListener('click', () => {
  progressCard.classList.add('hidden');
  reportCard.classList.add('hidden');
  document.getElementById('report-body').innerHTML = '';
  document.getElementById('report-docker').innerHTML = '';
  inputCard.classList.remove('hidden');
  lastReport     = null;
  pipelineErrors = [];
  selectedFile = null;
  dzName.classList.add('hidden');
  dropzone.classList.remove('has-file');
  btnZip.disabled = true;
  document.getElementById('git-url').value = '';
  document.getElementById('local-path').value = '';
});
