const state = {
  view: 'overview',
  profile: null,
  settings: null,
  statuses: [],
  sources: [],
  jobsPage: 1,
  jobsPages: 1,
  jobsPageSize: 25,
  jobsDebounce: null,
  runTimer: null,
  currentJobId: null,
};

const viewMeta = {
  overview: {
    number: '01',
    title: 'OVERVIEW',
    question: 'QUESTION: WHAT IS THE CURRENT STATE AND HISTORY OF THE MARKET DATABASE?',
  },
  jobs: {
    number: '02',
    title: 'JOBS',
    question: 'QUESTION: WHICH VACANCIES SHOULD I CONSIDER?',
  },
  applications: {
    number: '03',
    title: 'APPLICATIONS',
    question: 'QUESTION: WHERE DO MY SELECTED OPPORTUNITIES STAND?',
  },
  sources: {
    number: '04',
    title: 'SOURCES',
    question: 'QUESTION: WHERE DOES THE MARKET DATA COME FROM?',
  },
  pipeline: {
    number: '05',
    title: 'RUNS',
    question: 'QUESTION: IS THE COLLECTION ENGINE WORKING NOW?',
  },
  profile: {
    number: '06',
    title: 'PROFILE',
    question: 'QUESTION: WHY DOES THE SYSTEM SCORE A JOB THIS WAY?',
  },
};

function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function prettyStatus(value = '') {
  return String(value)
    .replaceAll('_', ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

function formatDate(value) {
  if (!value) return 'UNKNOWN';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'UNKNOWN';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  }).format(d).toUpperCase();
}

function formatDateTime(value) {
  if (!value) return 'NEVER';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'NEVER';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d).toUpperCase();
}

function scoreClass(score) {
  if (Number(score) >= 65) return 'score-high';
  if (Number(score) >= 45) return 'score-mid';
  return 'score-low';
}

function badgeClass(status) {
  if (['offer', 'applied', 'shortlisted', 'ready_to_apply', 'success'].includes(status)) return 'good';
  if (['application_preparing', 'interview', 'technical_test', 'hr_interview', 'running'].includes(status)) return 'info';
  if (['rejected', 'rejected_by_you', 'withdrawn', 'expired', 'failed'].includes(status)) return 'bad';
  if (['cooldown', 'skipped'].includes(status)) return 'warn';
  return 'neutral';
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === 'string'
        ? body.detail
        : body.detail?.message || JSON.stringify(body.detail || body);
    } catch (_) {}
    throw new Error(detail);
  }

  return response.json();
}

function showToast(message, error = false) {
  const el = document.getElementById('toast');
  el.textContent = `${error ? 'ERROR' : 'NOTICE'} :: ${message}`;
  el.style.borderColor = error ? '#d8b6aa' : '#d5ccb3';
  el.classList.remove('hidden');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.add('hidden'), 3200);
}

function setView(view) {
  state.view = view;

  document.querySelectorAll('.view').forEach(el => {
    el.classList.toggle('active', el.id === `view-${view}`);
  });

  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.view === view);
  });

  const meta = viewMeta[view];
  document.getElementById('viewNumber').textContent = meta.number;
  document.getElementById('viewTitle').textContent = meta.title;
  document.getElementById('viewSubtitle').textContent = meta.question;

  if (view === 'overview') loadOverview().catch(handleError);
  if (view === 'jobs') loadJobs().catch(handleError);
  if (view === 'applications') loadApplications().catch(handleError);
  if (view === 'sources') loadSources().catch(handleError);
  if (view === 'pipeline') loadPipeline().catch(handleError);
  if (view === 'profile') loadProfileView().catch(handleError);
}

function handleError(err) {
  showToast(err?.message || String(err), true);
}

function snapshotCell(index, label, value, note) {
  return `
    <div class="snapshot-cell">
      <div class="snapshot-index">${escapeHtml(index)}</div>
      <div class="snapshot-value">${escapeHtml(value)}</div>
      <div class="snapshot-label">${escapeHtml(label)}</div>
      <div class="snapshot-note">${escapeHtml(note || '')}</div>
    </div>`;
}

function registerHeader(columns) {
  return `
    <div class="register-row header">
      ${columns.map(col => `<div class="register-cell">${escapeHtml(col)}</div>`).join('')}
    </div>`;
}

function wireJobOpeners(root = document) {
  root.querySelectorAll('[data-job-id]').forEach(el => {
    el.addEventListener('click', () => openJob(Number(el.dataset.jobId)).catch(handleError));
  });
}

/* ------------------------------------------------------------------
   01 OVERVIEW
   Owns: database inventory + historical run outcomes.
   Does NOT own: job ranking, applications, source details or live runs.
------------------------------------------------------------------ */

async function loadOverview() {
  const [overview, history] = await Promise.all([
    api('/api/overview'),
    api('/api/run/history'),
  ]);

  const m = overview.metrics || {};
  document.getElementById('overviewSnapshot').innerHTML = [
    snapshotCell('01', 'TOTAL CANONICAL RECORDS', m.total_jobs ?? 0, 'ALL JOBS INCLUDING ARCHIVE'),
    snapshotCell('02', 'LIVE MARKET RECORDS', m.live_jobs ?? 0, 'CURRENT NON-STALE LISTINGS'),
    snapshotCell('03', 'STALE ARCHIVE', m.stale_jobs ?? 0, 'PRESERVED HISTORICAL LISTINGS'),
    snapshotCell('04', 'ELIGIBLE SCORE 50+', m.eligible_50_plus ?? 0, 'LIVE + ELIGIBILITY SCORE >= 50'),
  ].join('');

  renderOverviewRunLedger(history.items || []);
}

function renderOverviewRunLedger(items) {
  const el = document.getElementById('overviewRunLedger');

  if (!items.length) {
    el.innerHTML = `
      <div class="empty">
        NO DASHBOARD-LAUNCHED RUN HAS BEEN RECORDED YET.<br>
        THE INITIAL POWERSHELL DRY RUN REMAINS IN THE LOG FILES.
      </div>`;
    return;
  }

  el.classList.add('run-ledger');
  el.innerHTML =
    registerHeader(['STARTED', 'RESULT', 'RAW', 'NEW', 'MERGED', 'STALE', 'FAIL', 'LIMIT']) +
    items.map(run => {
      const summary = run.summary || {};
      return `
        <div class="register-row data">
          <div class="register-cell">${escapeHtml(formatDateTime(run.started_at))}</div>
          <div class="register-cell">
            <span class="status-stamp ${badgeClass(run.status)}">${escapeHtml(run.status || 'unknown')}</span>
          </div>
          <div class="register-cell">${summary.raw_jobs ?? 'â€”'}</div>
          <div class="register-cell">${summary.new_jobs ?? 'â€”'}</div>
          <div class="register-cell">${summary.duplicates ?? 'â€”'}</div>
          <div class="register-cell">${summary.stale ?? 'â€”'}</div>
          <div class="register-cell">${summary.collector_failures ?? 'â€”'}</div>
          <div class="register-cell">${run.limit ?? 'â€”'}</div>
        </div>`;
    }).join('');
}

/* ------------------------------------------------------------------
   02 JOBS
   Owns: opportunity discovery, ranking, filtering and job facts.
------------------------------------------------------------------ */

function populateSourceFilter() {
  const el = document.getElementById('jobsSource');
  if (!el) return;

  const current = el.value;
  el.innerHTML =
    '<option value="all">ALL SOURCES</option>' +
    state.sources
      .map(source => `<option value="${escapeHtml(source.name)}">${escapeHtml(source.name.toUpperCase())}</option>`)
      .join('');

  el.value = [...el.options].some(option => option.value === current)
    ? current
    : 'all';
}

async function loadJobs() {
  const params = new URLSearchParams({
    q: document.getElementById('jobsSearch').value,
    min_score: document.getElementById('jobsMinScore').value,
    source: document.getElementById('jobsSource').value,
    status: 'all',
    remote: document.getElementById('jobsRemote').value,
    freshness: document.getElementById('jobsFreshness').value,
    sort: document.getElementById('jobsSort').value,
    page: state.jobsPage,
    page_size: state.jobsPageSize,
  });

  const data = await api(`/api/jobs?${params}`);

  state.jobsPages = data.pages;
  state.jobsPage = data.page;

  document.getElementById('jobsCount').textContent =
    `${data.total} MATCHING RECORD${data.total === 1 ? '' : 'S'}`;

  document.getElementById('jobsPage').textContent =
    `PAGE ${data.page} / ${data.pages}`;

  document.getElementById('jobsPrev').disabled = data.page <= 1;
  document.getElementById('jobsNext').disabled = data.page >= data.pages;

  const table = document.getElementById('jobsTable');

  if (!data.items.length) {
    table.innerHTML = '<div class="empty">NO JOBS MATCH THE CURRENT PARAMETERS.</div>';
    return;
  }

  table.innerHTML =
    registerHeader(['SCORE', 'VACANCY', 'COMPANY', 'LOCATION', 'MODE', 'SOURCE', 'POSTED']) +
    data.items.map(job => {
      const source = (job.sources || [])[0]?.name || 'UNKNOWN';
      const seniority = job.seniority ? ` / ${String(job.seniority).toUpperCase()}` : '';
      const archived = job.stale ? ' / ARCHIVED' : '';
      return `
        <button class="register-row data" data-job-id="${job.id}">
          <div class="register-cell">
            <span class="score-stamp ${scoreClass(job.scores.overall)}">${job.scores.overall}</span>
          </div>
          <div class="register-cell">
            <div>
              <div class="job-title">${escapeHtml(job.title)}</div>
              <div class="job-subline">${escapeHtml((job.employment_type || 'UNSPECIFIED').toUpperCase())}${escapeHtml(seniority)}${escapeHtml(archived)}</div>
            </div>
          </div>
          <div class="register-cell company-cell">${escapeHtml(job.company)}</div>
          <div class="register-cell location-cell">${escapeHtml(job.location || 'UNKNOWN')}</div>
          <div class="register-cell">${job.remote ? 'REMOTE' : 'ON-SITE'}</div>
          <div class="register-cell source-cell">${escapeHtml(source.toUpperCase())}</div>
          <div class="register-cell date-cell">${escapeHtml(formatDate(job.posted_at))}</div>
        </button>`;
    }).join('');

  wireJobOpeners(table);
}

async function openJob(jobId) {
  state.currentJobId = jobId;

  const job = await api(`/api/jobs/${jobId}`);
  document.getElementById('jobModalTitle').textContent = job.title;
  document.getElementById('jobModalCompany').textContent =
    `${job.company} / ${job.location || 'UNKNOWN LOCATION'}`;

  const salary = job.salary_min
    ? `${job.salary_min}${job.salary_max ? ` - ${job.salary_max}` : ''} ${job.salary_currency || ''} ${job.salary_period || ''}`
    : 'NOT PROVIDED';

  const sources = (job.sources || [])
    .map(source => `
      <a class="badge info open-link"
         href="${escapeHtml(source.url)}"
         target="_blank"
         rel="noopener">${escapeHtml(source.name.toUpperCase())} [OPEN]</a>`)
    .join(' ');

  const statusOptions = state.statuses
    .map(status => `
      <option value="${status}" ${status === job.application_status ? 'selected' : ''}>
        ${escapeHtml(prettyStatus(status).toUpperCase())}
      </option>`)
    .join('');

  document.getElementById('jobModalBody').innerHTML = `
    <div class="job-detail-top">
      <div class="big-score ${scoreClass(job.scores.overall)}">${job.scores.overall}</div>
      <div class="score-bars">
        ${scoreBar('TECHNICAL', job.scores.technical)}
        ${scoreBar('ELIGIBILITY', job.scores.eligibility)}
        ${scoreBar('LOCATION', job.scores.location)}
        ${scoreBar('EXPERIENCE', job.scores.experience)}
      </div>
    </div>

    <div class="detail-grid">
      <div class="detail-item"><span>WORK MODE</span><strong>${job.remote ? 'REMOTE' : 'ON-SITE'}</strong></div>
      <div class="detail-item"><span>SENIORITY</span><strong>${escapeHtml((job.seniority || 'UNSPECIFIED').toUpperCase())}</strong></div>
      <div class="detail-item"><span>EMPLOYMENT</span><strong>${escapeHtml((job.employment_type || 'UNSPECIFIED').toUpperCase())}</strong></div>
      <div class="detail-item"><span>SALARY</span><strong>${escapeHtml(salary)}</strong></div>
      <div class="detail-item"><span>POSTED</span><strong>${escapeHtml(formatDate(job.posted_at))}</strong></div>
      <div class="detail-item"><span>FIRST SEEN</span><strong>${escapeHtml(formatDateTime(job.first_seen_at))}</strong></div>
    </div>

    <h3>MATCH EXPLANATION</h3>
    <div class="reasons">
      ${(job.score_explanations || []).length
        ? job.score_explanations.map(reason => `<div class="reason">${escapeHtml(reason)}</div>`).join('')
        : '<div class="reason">NO EXPLANATION STORED.</div>'}
    </div>

    <h3>SOURCE LINKS</h3>
    <div class="tag-list">${sources || '<span class="helper">NO SOURCE METADATA.</span>'}</div>

    <h3>CAPTURED DESCRIPTION</h3>
    <div class="description">${escapeHtml(job.description || 'NO DESCRIPTION CAPTURED.')}</div>

    <h3>TRACKING RECORD</h3>
    <div class="editor-grid">
      <label>
        <span>APPLICATION STATUS</span>
        <select id="editJobStatus">${statusOptions}</select>
      </label>
      <label>
        <span>CV VERSION</span>
        <input id="editCvVersion"
               value="${escapeHtml(job.cv_version || '')}"
               placeholder="EX: SOFTWARE-V3" />
      </label>
      <label class="full">
        <span>NOTES</span>
        <textarea id="editJobNotes"
                  placeholder="NEXT ACTION / CONTEXT / NOTES">${escapeHtml(job.notes || '')}</textarea>
      </label>
    </div>

    <div class="modal-actions">
      <a href="${escapeHtml(job.canonical_url)}"
         target="_blank"
         rel="noopener"
         class="button open-link">OPEN LISTING</a>
      <button class="button primary" id="saveJobButton">SAVE RECORD</button>
    </div>`;

  document.getElementById('jobModal').classList.remove('hidden');
  document.getElementById('saveJobButton')
    .addEventListener('click', () => saveCurrentJob().catch(handleError));
}

function scoreBar(label, score) {
  return `
    <div class="score-bar-row">
      <span>${escapeHtml(label)}</span>
      <div class="progress-track">
        <div class="progress-fill" style="width:${Number(score) || 0}%"></div>
      </div>
      <strong>${Number(score) || 0}</strong>
    </div>`;
}

async function saveCurrentJob() {
  if (!state.currentJobId) return;

  await api(`/api/jobs/${state.currentJobId}`, {
    method: 'PATCH',
    body: JSON.stringify({
      application_status: document.getElementById('editJobStatus').value,
      cv_version: document.getElementById('editCvVersion').value,
      notes: document.getElementById('editJobNotes').value,
    }),
  });

  showToast('JOB RECORD UPDATED');
  document.getElementById('jobModal').classList.add('hidden');
  await refreshCurrentView();
}

/* ------------------------------------------------------------------
   03 APPLICATIONS
   Owns: workflow state for jobs intentionally selected by the user.
------------------------------------------------------------------ */

async function loadApplications() {
  const data = await api('/api/applications');

  const groups = [
    ['SHORTLISTED', ['shortlisted']],
    ['PREPARING', ['application_preparing', 'ready_to_apply']],
    ['APPLIED', ['applied']],
    ['INTERVIEW', ['interview', 'technical_test', 'hr_interview']],
    ['OUTCOME', ['offer', 'rejected', 'withdrawn', 'expired', 'rejected_by_you']],
  ];

  const board = document.getElementById('applicationBoard');

  board.innerHTML = groups.map(([label, statuses]) => {
    const jobs = data.items.filter(job => statuses.includes(job.application_status));

    return `
      <div class="kanban-column">
        <div class="kanban-head">
          <span class="kanban-title">${label}</span>
          <span class="badge neutral">${jobs.length}</span>
        </div>

        ${jobs.length
          ? jobs.map(job => `
              <button class="kanban-card" data-job-id="${job.id}">
                <div class="kanban-card-title">${escapeHtml(job.title)}</div>
                <div class="kanban-card-meta">${escapeHtml(job.company)}</div>
                <div class="kanban-card-meta">${escapeHtml(prettyStatus(job.application_status).toUpperCase())}</div>
              </button>`).join('')
          : '<div class="empty">EMPTY</div>'}
      </div>`;
  }).join('');

  wireJobOpeners(board);
}

/* ------------------------------------------------------------------
   04 SOURCES
   Owns: collector coverage and polling availability.
------------------------------------------------------------------ */

async function fetchSources() {
  const data = await api('/api/sources');
  state.sources = data.items || [];
  populateSourceFilter();
  return state.sources;
}

async function loadSources() {
  const sources = await fetchSources();
  const table = document.getElementById('sourcesTable');

  if (!sources.length) {
    table.innerHTML = '<div class="empty">NO SOURCES CONFIGURED.</div>';
    return;
  }

  table.innerHTML =
    registerHeader([
      'SOURCE',
      'TYPE',
      'STATE',
      'SEEN',
      'LIVE',
      'INTERVAL',
      'LAST POLL',
      'NEXT POLL',
      'NOTE',
    ]) +
    sources.map(source => {
      const stateText = !source.enabled
        ? 'DISABLED'
        : source.eligible_now
          ? 'READY'
          : 'COOLDOWN';

      const stateClass = !source.enabled
        ? 'bad'
        : source.eligible_now
          ? 'good'
          : 'warn';

      return `
        <div class="register-row data">
          <div class="register-cell"><strong>${escapeHtml(source.name.toUpperCase())}</strong></div>
          <div class="register-cell">${escapeHtml((source.kind || 'â€”').toUpperCase())}</div>
          <div class="register-cell"><span class="status-stamp ${stateClass}">${stateText}</span></div>
          <div class="register-cell">${source.jobs_seen ?? 0}</div>
          <div class="register-cell">${source.live_jobs ?? 0}</div>
          <div class="register-cell">${source.min_interval_minutes ?? 0} MIN</div>
          <div class="register-cell">${escapeHtml(formatDateTime(source.last_polled_at))}</div>
          <div class="register-cell">${source.eligible_now ? 'NOW' : escapeHtml(formatDateTime(source.next_eligible_at))}</div>
          <div class="register-cell">${escapeHtml(source.note || 'â€”')}</div>
        </div>`;
    }).join('');

  updateRunEligibility();
}

function updateRunEligibility() {
  const enabled = state.sources.filter(source => source.enabled);
  const ready = enabled.filter(source => source.eligible_now);
  const text = document.getElementById('runEligibilityText');
  const button = document.getElementById('runButtonPipeline');

  const running =
    document.getElementById('runStatusBadge')?.textContent?.toLowerCase() === 'running';

  if (!text || !button) return;

  if (ready.length) {
    text.textContent =
      `${ready.length}/${enabled.length} ENABLED SOURCE(S) AVAILABLE NOW`;
    button.disabled = running;
    return;
  }

  if (enabled.length) {
    const next = enabled
      .map(source => source.next_eligible_at)
      .filter(Boolean)
      .sort()[0];

    text.textContent =
      `ALL SOURCES IN COOLDOWN / NEXT: ${formatDateTime(next)}`;
    button.disabled = true;
    return;
  }

  text.textContent = 'NO ENABLED SOURCES';
  button.disabled = true;
}

/* ------------------------------------------------------------------
   05 RUNS
   Owns: only the currently executing collection operation.
   Historical run outcomes live in OVERVIEW.
------------------------------------------------------------------ */

async function startCollection() {
  const limit = Number(document.getElementById('runLimit')?.value || 100);

  try {
    await api('/api/run', {
      method: 'POST',
      body: JSON.stringify({
        limit,
        ignore_poll_interval: false,
      }),
    });

    showToast('COLLECTION STARTED');
    setView('pipeline');
    beginRunPolling();
  } catch (err) {
    showToast(err.message, true);
    await fetchSources();
    updateRunEligibility();
  }
}

function renderRunState(run) {
  const status = run.status || 'idle';
  const badge = document.getElementById('runStatusBadge');

  badge.textContent = status.toUpperCase();
  badge.className = `status-stamp ${badgeClass(status)}`;
  document.getElementById('runPhase').textContent =
    (run.phase || 'IDLE').toUpperCase();

  const sources = Object.entries(run.sources || {});
  const progress = document.getElementById('runSourceProgress');

  progress.innerHTML = sources.length
    ? sources.map(([name, source]) => {
        const sourceStatus = source.status || 'queued';
        const pct =
          ['success', 'failed', 'skipped'].includes(sourceStatus)
            ? 100
            : sourceStatus === 'running'
              ? 55
              : 8;

        const fetched = Number.isFinite(source.fetched)
          ? ` / ${source.fetched}`
          : '';

        return `
          <div class="source-progress-row">
            <span class="source-name">${escapeHtml(name)}</span>
            <div class="progress-track">
              <div class="progress-fill" style="width:${pct}%"></div>
            </div>
            <span class="status-stamp ${badgeClass(sourceStatus)}">
              ${escapeHtml(sourceStatus)}${escapeHtml(fetched)}
            </span>
          </div>`;
      }).join('')
    : '<div class="empty">NO ACTIVE SOURCE PROCESS.</div>';

  const logs = (run.logs || []).slice(-160);
  const log = document.getElementById('runLogs');
  log.textContent = logs.length
    ? logs.join('\n')
    : 'NO UI-MANAGED RUN YET.';
  log.scrollTop = log.scrollHeight;

  const running = status === 'running';
  document.getElementById('runButtonPipeline').disabled = running;
}

async function loadPipeline() {
  await fetchSources();
  updateRunEligibility();

  const run = await api('/api/run/status');
  renderRunState(run);

  if (run.status === 'running') {
    beginRunPolling();
  }
}

function beginRunPolling() {
  clearInterval(state.runTimer);

  state.runTimer = setInterval(async () => {
    try {
      const run = await api('/api/run/status');
      renderRunState(run);

      if (run.status !== 'running') {
        clearInterval(state.runTimer);
        state.runTimer = null;

        await fetchSources();
        updateRunEligibility();

        showToast(
          run.status === 'success'
            ? 'COLLECTION COMPLETED / RESULT RECORDED IN OVERVIEW'
            : 'COLLECTION FINISHED WITH AN ERROR',
          run.status !== 'success',
        );
      }
    } catch (_) {}
  }, 1200);
}

/* ------------------------------------------------------------------
   06 PROFILE
   Owns: user assumptions + ranking configuration.
------------------------------------------------------------------ */

async function loadProfileView() {
  const data = await api('/api/profile');

  state.profile = data.profile;
  state.settings = data.settings;
  state.statuses = data.application_statuses || [];

  const p = data.profile || {};
  const s = data.settings || {};
  const weights = s.matching?.overall_weights || {};

  const education = (p.education || [])
    .map(item => `
      <div class="profile-record">
        <strong>${escapeHtml((item.degree_level || '').toUpperCase())}</strong>
        ${escapeHtml(item.field || '')}<br>
        ${escapeHtml(item.institution || '')}
      </div>`)
    .join('');

  const experiences = (p.experience_highlights || [])
    .map(item => `<div class="profile-record">${escapeHtml(item)}</div>`)
    .join('');

  document.getElementById('profileSummary').innerHTML = `
    <div class="profile-block">
      <h3>IDENTITY / RESIDENCE</h3>
      <div class="profile-record">
        <strong>${escapeHtml((p.name || '').toUpperCase())}</strong>
        ${escapeHtml((p.country_of_residence || '').toUpperCase())}
      </div>
    </div>

    <div class="profile-block">
      <h3>LANGUAGES</h3>
      <div class="tag-list">
        ${Object.entries(p.languages || {})
          .map(([language, level]) => `
            <span class="badge info">${escapeHtml(language)} ${escapeHtml(level)}</span>`)
          .join('')}
      </div>
    </div>

    <div class="profile-block">
      <h3>SKILLS USED FOR MATCHING</h3>
      <div class="tag-list">
        ${(p.skills || [])
          .map(skill => `<span class="badge neutral">${escapeHtml(skill)}</span>`)
          .join('')}
      </div>
    </div>

    <div class="profile-block">
      <h3>EDUCATION</h3>
      ${education || '<div class="helper">NO EDUCATION RECORDS.</div>'}
    </div>

    <div class="profile-block">
      <h3>EXPERIENCE SIGNALS</h3>
      ${experiences || '<div class="helper">NO EXPERIENCE SIGNALS.</div>'}
    </div>`;

  const localLocations = p.geography?.allowed_local_locations || [];
  const allowedSeniorities = p.seniority?.allowed || [];
  const excludedLanguages = p.excluded_languages_when_required || [];

  document.getElementById('settingsSummary').innerHTML = `
    <div class="profile-block">
      <h3>MARKET / ELIGIBILITY RULES</h3>
      <div class="rule-list">
        <div class="rule"><span>LOCAL COUNTRY</span><strong>${escapeHtml(p.geography?.local_country || 'â€”')}</strong></div>
        <div class="rule"><span>INTERNATIONAL ON-SITE</span><strong>${p.geography?.international_onsite ? 'ALLOW' : 'REJECT'}</strong></div>
        <div class="rule"><span>WORLDWIDE REMOTE</span><strong>${p.geography?.remote_worldwide ? 'ALLOW' : 'REJECT'}</strong></div>
        <div class="rule"><span>RELOCATION</span><strong>${p.geography?.relocation ? 'ALLOW' : 'REJECT'}</strong></div>
        <div class="rule"><span>VISA SPONSORSHIP TARGET</span><strong>${p.geography?.visa_sponsorship ? 'YES' : 'NO'}</strong></div>
        <div class="rule"><span>EXCLUDED REQUIRED LANGUAGES</span><strong>${escapeHtml(excludedLanguages.join(', ') || 'NONE')}</strong></div>
        <div class="rule"><span>ALLOWED SENIORITY</span><strong>${escapeHtml(allowedSeniorities.join(', ') || 'â€”')}</strong></div>
      </div>
    </div>

    <div class="profile-block">
      <h3>GREATER TUNIS ACCEPTED LOCATIONS</h3>
      <div class="tag-list">
        ${localLocations.map(location => `<span class="badge neutral">${escapeHtml(location)}</span>`).join('')}
      </div>
    </div>

    <div class="profile-block">
      <h3>SCORE POLICY</h3>
      <div class="rule-list">
        <div class="rule"><span>SHORTLIST THRESHOLD</span><strong>${s.matching?.shortlist_threshold ?? 65}/100</strong></div>
        <div class="rule"><span>TECHNICAL WEIGHT</span><strong>${Math.round((weights.technical || 0) * 100)}%</strong></div>
        <div class="rule"><span>ELIGIBILITY WEIGHT</span><strong>${Math.round((weights.eligibility || 0) * 100)}%</strong></div>
        <div class="rule"><span>LOCATION WEIGHT</span><strong>${Math.round((weights.location || 0) * 100)}%</strong></div>
        <div class="rule"><span>EXPERIENCE WEIGHT</span><strong>${Math.round((weights.experience || 0) * 100)}%</strong></div>
        <div class="rule"><span>FRESHNESS WINDOW</span><strong>${s.collection?.stale_after_days ?? 45} DAYS</strong></div>
        <div class="rule"><span>UNKNOWN SALARY</span><strong>${escapeHtml((s.matching?.unknown_salary_policy || 'KEEP').toUpperCase())}</strong></div>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------
   Shared refresh / bootstrapping
------------------------------------------------------------------ */

async function refreshCurrentView() {
  if (state.view === 'overview') return loadOverview();
  if (state.view === 'jobs') return loadJobs();
  if (state.view === 'applications') return loadApplications();
  if (state.view === 'sources') return loadSources();
  if (state.view === 'pipeline') return loadPipeline();
  if (state.view === 'profile') return loadProfileView();
}

function scheduleJobsReload() {
  clearTimeout(state.jobsDebounce);
  state.jobsDebounce = setTimeout(() => {
    state.jobsPage = 1;
    loadJobs().catch(handleError);
  }, 250);
}

async function bootstrap() {
  try {
    const [health, profile, sources] = await Promise.all([
      api('/api/health'),
      api('/api/profile'),
      api('/api/sources'),
    ]);

    document.getElementById('connectionDot').classList.add('online');
    document.getElementById('connectionText').textContent = 'LOCAL API / ONLINE';
    document.getElementById('appVersion').textContent =
      `VERSION ${health.version}`;

    state.profile = profile.profile;
    state.settings = profile.settings;
    state.statuses = profile.application_statuses || [];
    state.sources = sources.items || [];

    populateSourceFilter();
    await loadOverview();

    const run = await api('/api/run/status');
    renderRunState(run);

    if (run.status === 'running') {
      beginRunPolling();
    }
  } catch (err) {
    document.getElementById('connectionDot').classList.add('offline');
    document.getElementById('connectionText').textContent = 'LOCAL API / OFFLINE';
    showToast(err.message, true);
  }
}

/* Navigation */
document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', () => setView(el.dataset.view));
});

/* Global refresh */
document.getElementById('refreshButton')
  .addEventListener('click', () => refreshCurrentView().catch(handleError));

/* Runs */
document.getElementById('runButtonPipeline')
  .addEventListener('click', () => startCollection().catch(handleError));

/* Job modal */
document.getElementById('closeJobModal')
  .addEventListener('click', () => {
    document.getElementById('jobModal').classList.add('hidden');
  });

document.getElementById('jobModal')
  .addEventListener('click', event => {
    if (event.target.id === 'jobModal') {
      event.currentTarget.classList.add('hidden');
    }
  });

document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    document.getElementById('jobModal').classList.add('hidden');
  }
});

/* Jobs filters */
document.getElementById('jobsSearch')
  .addEventListener('input', scheduleJobsReload);

[
  'jobsMinScore',
  'jobsSource',
  'jobsRemote',
  'jobsFreshness',
  'jobsSort',
].forEach(id => {
  document.getElementById(id)
    .addEventListener('change', scheduleJobsReload);
});

document.getElementById('jobsPrev')
  .addEventListener('click', () => {
    if (state.jobsPage > 1) {
      state.jobsPage--;
      loadJobs().catch(handleError);
    }
  });

document.getElementById('jobsNext')
  .addEventListener('click', () => {
    if (state.jobsPage < state.jobsPages) {
      state.jobsPage++;
      loadJobs().catch(handleError);
    }
  });

bootstrap();
