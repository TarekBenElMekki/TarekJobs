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
  overview: ['Overview', 'Your live view of the job market and application pipeline.'],
  jobs: ['Jobs', 'Search, filter, rank and inspect every canonical listing.'],
  applications: ['Applications', 'Move opportunities from shortlist to offer and keep the history visible.'],
  sources: ['Sources', 'Coverage and polling health for each collector.'],
  pipeline: ['Pipeline', 'Launch collection runs and watch progress source by source.'],
  profile: ['Profile', 'The rules currently driving eligibility and ranking.'],
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
  return value.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatDate(value) {
  if (!value) return 'Unknown';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'Unknown';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(d);
}

function formatDateTime(value) {
  if (!value) return 'Never';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'Never';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(d);
}

function relativeTime(value) {
  if (!value) return 'Unknown';
  const d = new Date(value);
  const seconds = Math.round((Date.now() - d.getTime()) / 1000);
  if (Math.abs(seconds) < 60) return `${Math.abs(seconds)}s ago`;
  const mins = Math.round(seconds / 60);
  if (Math.abs(mins) < 60) return `${Math.abs(mins)}m ago`;
  const hours = Math.round(mins / 60);
  if (Math.abs(hours) < 24) return `${Math.abs(hours)}h ago`;
  const days = Math.round(hours / 24);
  return `${Math.abs(days)}d ago`;
}

function scoreClass(score) {
  if (score >= 65) return 'score-high';
  if (score >= 45) return 'score-mid';
  return 'score-low';
}

function badgeClass(status) {
  if (['offer', 'applied', 'shortlisted', 'ready_to_apply'].includes(status)) return 'good';
  if (['application_preparing', 'interview', 'technical_test', 'hr_interview'].includes(status)) return 'info';
  if (['rejected', 'rejected_by_you', 'withdrawn', 'expired'].includes(status)) return 'bad';
  return 'neutral';
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === 'string' ? body.detail : body.detail?.message || JSON.stringify(body.detail || body);
    } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function showToast(message, error = false) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.style.borderColor = error ? 'rgba(251,113,133,.45)' : 'rgba(94,234,212,.35)';
  el.classList.remove('hidden');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.add('hidden'), 3200);
}

function setView(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === `view-${view}`));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  const [title, subtitle] = viewMeta[view];
  document.getElementById('viewTitle').textContent = title;
  document.getElementById('viewSubtitle').textContent = subtitle;
  if (view === 'jobs') loadJobs();
  if (view === 'applications') loadApplications();
  if (view === 'sources') loadSources();
  if (view === 'pipeline') loadPipeline();
  if (view === 'profile') loadProfileView();
}

function metricCard(label, value, note = '') {
  return `<div class="metric-card"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${escapeHtml(value)}</div><div class="metric-note">${escapeHtml(note)}</div></div>`;
}

function jobCompact(job) {
  const sources = (job.sources || []).map(s => s.name).join(', ') || 'unknown';
  return `<button class="job-row reset-button" data-job-id="${job.id}">
    <div class="score-badge ${scoreClass(job.scores.overall)}">${job.scores.overall}</div>
    <div class="align-left">
      <div class="job-title">${escapeHtml(job.title)}</div>
      <div class="job-meta"><span>${escapeHtml(job.company)}</span><span>•</span><span>${escapeHtml(job.location || 'Location unknown')}</span><span>•</span><span>${escapeHtml(sources)}</span></div>
    </div>
    <span class="badge ${badgeClass(job.application_status)}">${escapeHtml(prettyStatus(job.application_status))}</span>
  </button>`;
}

function wireJobOpeners(root = document) {
  root.querySelectorAll('[data-job-id]').forEach(el => {
    el.addEventListener('click', () => openJob(Number(el.dataset.jobId)));
  });
}

async function loadOverview() {
  const data = await api('/api/overview');
  const m = data.metrics;
  document.getElementById('overviewMetrics').innerHTML = [
    metricCard('Live jobs', m.live_jobs, `${m.stale_jobs} stale preserved`),
    metricCard(`Strong matches ${data.shortlist_threshold}+`, m.strong_matches, 'After eligibility and location gates'),
    metricCard('Active pipeline', m.active_pipeline, 'Shortlisted through offer'),
    metricCard('Applied or beyond', m.applied_or_beyond, 'Submitted applications and interviews'),
  ].join('');

  const topJobs = document.getElementById('topJobs');
  topJobs.innerHTML = data.top_jobs.length ? data.top_jobs.map(jobCompact).join('') : '<div class="empty">No live jobs yet.</div>';
  wireJobOpeners(topJobs);

  const counts = data.status_counts || {};
  const stages = [
    ['Discovered', counts.discovered || 0],
    ['Shortlisted', counts.shortlisted || 0],
    ['Preparing', (counts.application_preparing || 0) + (counts.ready_to_apply || 0)],
    ['Applied', counts.applied || 0],
    ['Interview', (counts.interview || 0) + (counts.technical_test || 0) + (counts.hr_interview || 0)],
    ['Offer', counts.offer || 0],
  ];
  const max = Math.max(...stages.map(x => x[1]), 1);
  document.getElementById('funnel').innerHTML = stages.map(([label, count]) => `<div class="funnel-row"><span>${label}</span><div class="funnel-track"><div class="funnel-fill" style="width:${Math.max(count ? 5 : 0, count / max * 100)}%"></div></div><strong>${count}</strong></div>`).join('');

  state.sources = data.sources || [];
  document.getElementById('sourceOverview').innerHTML = state.sources.map(s => `<div class="source-mini"><div class="source-mini-top"><span class="source-name">${escapeHtml(s.name)}</span><span class="badge ${s.eligible_now ? 'good' : 'warn'}">${s.eligible_now ? 'ready' : 'cooldown'}</span></div><div class="source-count">${s.live_jobs}</div><div class="source-caption">live source records · ${s.jobs_seen} total</div></div>`).join('');

  const recent = document.getElementById('recentJobs');
  recent.innerHTML = data.recent_jobs.length ? data.recent_jobs.map(j => `<button class="activity-item reset-button align-left" data-job-id="${j.id}"><div class="job-title">${escapeHtml(j.title)}</div><div class="job-meta"><span>${escapeHtml(j.company)}</span><span>•</span><span>${escapeHtml(j.location || 'Unknown')}</span></div><div class="activity-time">Discovered ${relativeTime(j.first_seen_at)}</div></button>`).join('') : '<div class="empty">No recent jobs.</div>';
  wireJobOpeners(recent);

  populateSourceFilter();
  renderRunState(data.run || {});
}

function populateSourceFilter() {
  const el = document.getElementById('jobsSource');
  const current = el.value;
  el.innerHTML = '<option value="all">All sources</option>' + state.sources.map(s => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)}</option>`).join('');
  el.value = [...el.options].some(o => o.value === current) ? current : 'all';
}

async function loadJobs() {
  const params = new URLSearchParams({
    q: document.getElementById('jobsSearch').value,
    min_score: document.getElementById('jobsMinScore').value,
    source: document.getElementById('jobsSource').value,
    status: document.getElementById('jobsStatus').value,
    remote: document.getElementById('jobsRemote').value,
    freshness: document.getElementById('jobsFreshness').value,
    sort: document.getElementById('jobsSort').value,
    page: state.jobsPage,
    page_size: state.jobsPageSize,
  });
  const data = await api(`/api/jobs?${params}`);
  state.jobsPages = data.pages;
  state.jobsPage = data.page;
  document.getElementById('jobsCount').textContent = `${data.total} matching listing${data.total === 1 ? '' : 's'}`;
  document.getElementById('jobsPage').textContent = `Page ${data.page} of ${data.pages}`;
  document.getElementById('jobsPrev').disabled = data.page <= 1;
  document.getElementById('jobsNext').disabled = data.page >= data.pages;

  const table = document.getElementById('jobsTable');
  if (!data.items.length) {
    table.innerHTML = '<div class="empty">No jobs match these filters.</div>';
    return;
  }
  table.innerHTML = `<div class="job-table-row header"><div>Score</div><div>Job</div><div>Company</div><div>Location</div><div>Status</div><div>Source</div></div>` + data.items.map(job => {
    const source = (job.sources || [])[0]?.name || '';
    return `<button class="job-table-row data reset-button" data-job-id="${job.id}">
      <div><span class="score-badge ${scoreClass(job.scores.overall)}">${job.scores.overall}</span></div>
      <div class="align-left"><div class="job-title">${escapeHtml(job.title)}</div><div class="job-meta"><span>${job.remote ? 'Remote' : 'On-site'}</span>${job.seniority ? `<span>• ${escapeHtml(job.seniority)}</span>` : ''}${job.stale ? '<span>• stale</span>' : ''}</div></div>
      <div class="company-cell align-left">${escapeHtml(job.company)}</div>
      <div class="location-cell align-left">${escapeHtml(job.location || 'Unknown')}</div>
      <div class="align-left"><span class="badge ${badgeClass(job.application_status)}">${escapeHtml(prettyStatus(job.application_status))}</span></div>
      <div class="align-left">${escapeHtml(source)}</div>
    </button>`;
  }).join('');
  wireJobOpeners(table);
}

async function openJob(jobId) {
  state.currentJobId = jobId;
  const job = await api(`/api/jobs/${jobId}`);
  document.getElementById('jobModalTitle').textContent = job.title;
  document.getElementById('jobModalCompany').textContent = `${job.company} · ${job.location || 'Unknown location'}`;
  const salary = job.salary_min ? `${job.salary_min}${job.salary_max ? ` – ${job.salary_max}` : ''} ${job.salary_currency || ''} ${job.salary_period || ''}` : 'Not provided';
  const sources = (job.sources || []).map(s => `<a class="badge info open-link" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.name)} ↗</a>`).join(' ');
  const statusOptions = state.statuses.map(s => `<option value="${s}" ${s === job.application_status ? 'selected' : ''}>${prettyStatus(s)}</option>`).join('');
  document.getElementById('jobModalBody').innerHTML = `
    <div class="job-detail-top">
      <div class="big-score ${scoreClass(job.scores.overall)}">${job.scores.overall}</div>
      <div class="score-bars">
        ${scoreBar('Technical', job.scores.technical)}
        ${scoreBar('Eligibility', job.scores.eligibility)}
        ${scoreBar('Location', job.scores.location)}
        ${scoreBar('Experience', job.scores.experience)}
      </div>
    </div>
    <div class="detail-grid">
      <div class="detail-item"><span>Work mode</span><strong>${job.remote ? 'Remote' : 'On-site'}</strong></div>
      <div class="detail-item"><span>Seniority</span><strong>${escapeHtml(job.seniority || 'Unspecified')}</strong></div>
      <div class="detail-item"><span>Employment</span><strong>${escapeHtml(job.employment_type || 'Unspecified')}</strong></div>
      <div class="detail-item"><span>Salary</span><strong>${escapeHtml(salary)}</strong></div>
      <div class="detail-item"><span>Posted</span><strong>${formatDate(job.posted_at)}</strong></div>
      <div class="detail-item"><span>First seen</span><strong>${formatDateTime(job.first_seen_at)}</strong></div>
    </div>
    <h3>Why it scored this way</h3>
    <div class="reasons">${(job.score_explanations || []).map(r => `<div class="reason">${escapeHtml(r)}</div>`).join('') || '<div class="helper">No explanation stored.</div>'}</div>
    <h3>Sources</h3>
    <div class="tag-list">${sources || '<span class="helper">No source metadata.</span>'}</div>
    <h3>Description</h3>
    <div class="description">${escapeHtml(job.description || 'No description captured.')}</div>
    <div class="editor-grid">
      <label><span>Status</span><select id="editJobStatus">${statusOptions}</select></label>
      <label><span>CV version</span><input id="editCvVersion" value="${escapeHtml(job.cv_version || '')}" placeholder="e.g. software-v3" /></label>
      <label class="full"><span>Notes</span><textarea id="editJobNotes" placeholder="Why this role matters, recruiter notes, next action…">${escapeHtml(job.notes || '')}</textarea></label>
    </div>
    <div class="modal-actions">
      <a href="${escapeHtml(job.canonical_url)}" target="_blank" rel="noopener" class="button secondary open-link">Open listing ↗</a>
      <button class="button primary" id="saveJobButton">Save changes</button>
    </div>`;
  document.getElementById('jobModal').classList.remove('hidden');
  document.getElementById('saveJobButton').addEventListener('click', saveCurrentJob);
}

function scoreBar(label, score) {
  return `<div class="score-bar-row"><span>${label}</span><div class="progress-track"><div class="progress-fill" style="width:${score}%"></div></div><strong>${score}</strong></div>`;
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
  showToast('Job updated');
  document.getElementById('jobModal').classList.add('hidden');
  await refreshCurrentView();
}

async function loadApplications() {
  const data = await api('/api/applications');
  const c = data.status_counts || {};
  document.getElementById('applicationMetrics').innerHTML = [
    metricCard('Shortlisted', c.shortlisted || 0, 'Worth acting on'),
    metricCard('Preparing', (c.application_preparing || 0) + (c.ready_to_apply || 0), 'Application work in progress'),
    metricCard('Applied', c.applied || 0, 'Submitted'),
    metricCard('Interviews', (c.interview || 0) + (c.technical_test || 0) + (c.hr_interview || 0), 'Active interview process'),
  ].join('');

  const groups = [
    ['Shortlisted', ['shortlisted']],
    ['Preparing', ['application_preparing', 'ready_to_apply']],
    ['Applied', ['applied']],
    ['Interview', ['interview', 'technical_test', 'hr_interview']],
    ['Outcome', ['offer', 'rejected', 'withdrawn', 'expired', 'rejected_by_you']],
  ];
  const board = document.getElementById('applicationBoard');
  board.innerHTML = groups.map(([label, statuses]) => {
    const jobs = data.items.filter(j => statuses.includes(j.application_status));
    return `<div class="kanban-column"><div class="kanban-head"><span class="kanban-title">${label}</span><span class="badge neutral">${jobs.length}</span></div>${jobs.length ? jobs.map(j => `<button class="kanban-card reset-button align-left" data-job-id="${j.id}"><div class="kanban-card-title">${escapeHtml(j.title)}</div><div class="kanban-card-meta">${escapeHtml(j.company)} · score ${j.scores.overall}</div><div class="kanban-card-meta">${escapeHtml(prettyStatus(j.application_status))}</div></button>`).join('') : '<div class="empty">Empty</div>'}</div>`;
  }).join('');
  wireJobOpeners(board);
}

async function loadSources() {
  const data = await api('/api/sources');
  state.sources = data.items;
  const totalRecords = state.sources.reduce((a, s) => a + s.jobs_seen, 0);
  const liveRecords = state.sources.reduce((a, s) => a + s.live_jobs, 0);
  const ready = state.sources.filter(s => s.enabled && s.eligible_now).length;
  document.getElementById('sourceMetrics').innerHTML = [
    metricCard('Enabled collectors', state.sources.filter(s => s.enabled).length, `${state.sources.length} configured`),
    metricCard('Source records', totalRecords, 'Includes duplicate appearances'),
    metricCard('Live source records', liveRecords, 'Backed by non-stale jobs'),
    metricCard('Ready to poll', ready, 'Respecting minimum intervals'),
  ].join('');

  document.getElementById('sourcesGrid').innerHTML = state.sources.map(s => `<div class="source-card">
    <div class="source-card-head"><div><div class="source-name">${escapeHtml(s.name)}</div><div class="helper">${escapeHtml(s.kind)} collector</div></div><span class="badge ${!s.enabled ? 'bad' : s.eligible_now ? 'good' : 'warn'}">${!s.enabled ? 'disabled' : s.eligible_now ? 'ready' : 'cooldown'}</span></div>
    <div class="source-card-stats"><div class="source-stat"><strong>${s.jobs_seen}</strong><span>source records</span></div><div class="source-stat"><strong>${s.live_jobs}</strong><span>live records</span></div><div class="source-stat"><strong>${s.max_jobs}</strong><span>max / dry run</span></div></div>
    <div class="source-note">${escapeHtml(s.note || '')}</div>
    <div class="source-note">Last poll: ${formatDateTime(s.last_polled_at)}<br>Next eligible: ${s.eligible_now ? 'now' : formatDateTime(s.next_eligible_at)}</div>
  </div>`).join('');
  populateSourceFilter();
  updateRunEligibility();
}

function updateRunEligibility() {
  const enabled = state.sources.filter(s => s.enabled);
  const ready = enabled.filter(s => s.eligible_now);
  const el = document.getElementById('runEligibilityText');
  const buttons = [document.getElementById('runButton'), document.getElementById('runButtonPipeline')];
  const running = document.getElementById('runStatusBadge')?.textContent === 'running';
  if (ready.length) {
    el.textContent = `${ready.length}/${enabled.length} enabled source(s) can be polled now.`;
    buttons.forEach(b => { if (b) b.disabled = running; });
  } else if (enabled.length) {
    const next = enabled.map(s => s.next_eligible_at).filter(Boolean).sort()[0];
    el.textContent = `All sources are cooling down. Next eligible: ${formatDateTime(next)}.`;
    buttons.forEach(b => { if (b) b.disabled = true; });
  }
}

async function startCollection() {
  const limit = Number(document.getElementById('runLimit')?.value || 100);
  try {
    await api('/api/run', { method: 'POST', body: JSON.stringify({ limit, ignore_poll_interval: false }) });
    showToast('Collection started');
    setView('pipeline');
    beginRunPolling();
  } catch (err) {
    showToast(err.message, true);
    await loadSources();
  }
}

function renderRunState(run) {
  const status = run.status || 'idle';
  document.getElementById('runStatusBadge').textContent = status;
  document.getElementById('runStatusBadge').className = `badge ${status === 'success' ? 'good' : status === 'failed' ? 'bad' : status === 'running' ? 'info' : 'neutral'}`;
  document.getElementById('runPhase').textContent = run.phase || 'Idle';

  const sources = Object.entries(run.sources || {});
  document.getElementById('runSourceProgress').innerHTML = sources.length ? sources.map(([name, s]) => {
    const statusText = s.status || 'queued';
    const pct = statusText === 'success' || statusText === 'failed' || statusText === 'skipped' ? 100 : statusText === 'running' ? 55 : 8;
    return `<div class="source-progress-row"><span class="source-name">${escapeHtml(name)}</span><div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div><span class="badge ${statusText === 'success' ? 'good' : statusText === 'failed' ? 'bad' : statusText === 'running' ? 'info' : statusText === 'skipped' ? 'warn' : 'neutral'}">${escapeHtml(statusText)}${Number.isFinite(s.fetched) ? ` ${s.fetched}` : ''}</span></div>`;
  }).join('') : '<div class="helper">No UI-managed run yet.</div>';

  const summary = run.summary || {};
  const summaryItems = [
    ['Raw', summary.raw_jobs], ['New', summary.new_jobs], ['Merged', summary.duplicates],
    ['Seen again', summary.seen_again], ['Stale', summary.stale], ['Live export', summary.live_exported],
  ].filter(([,v]) => v !== undefined);
  document.getElementById('runSummary').innerHTML = summaryItems.map(([k,v]) => `<div class="run-summary-item"><strong>${v}</strong><span>${k}</span></div>`).join('');
  document.getElementById('runLogs').textContent = (run.logs || []).slice(-120).join('\n') || 'No UI-managed run yet.';
  const logEl = document.getElementById('runLogs');
  logEl.scrollTop = logEl.scrollHeight;

  const running = status === 'running';
  [document.getElementById('runButton'), document.getElementById('runButtonPipeline')].forEach(b => { if (b) b.disabled = running; });
}

async function loadPipeline() {
  await loadSources();
  const [run, history] = await Promise.all([api('/api/run/status'), api('/api/run/history')]);
  renderRunState(run);
  renderRunHistory(history.items || []);
  if (run.status === 'running') beginRunPolling();
}

function renderRunHistory(items) {
  const el = document.getElementById('runHistory');
  if (!items.length) { el.innerHTML = '<div class="empty">No dashboard-launched runs yet.</div>'; return; }
  el.innerHTML = items.map(r => `<div class="history-item"><span>${formatDateTime(r.started_at)}</span><span class="badge ${r.status === 'success' ? 'good' : 'bad'}">${escapeHtml(r.status)}</span><span>${r.summary?.raw_jobs ?? 0} raw · ${r.summary?.new_jobs ?? 0} new · ${r.summary?.duplicates ?? 0} merged</span><span>${r.limit || ''} max</span></div>`).join('');
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
        await Promise.all([loadOverview(), loadSources()]);
        const history = await api('/api/run/history');
        renderRunHistory(history.items || []);
        showToast(run.status === 'success' ? 'Collection completed' : 'Collection finished with an error', run.status !== 'success');
      }
    } catch (_) {}
  }, 1200);
}

async function loadProfileView() {
  const data = await api('/api/profile');
  state.profile = data.profile;
  state.settings = data.settings;
  state.statuses = data.application_statuses || [];
  populateStatusFilter();

  const p = data.profile;
  document.getElementById('profileSummary').innerHTML = `
    <div class="profile-block"><h3>${escapeHtml(p.name || '')}</h3><div class="helper">Residence: ${escapeHtml(p.country_of_residence || '')}</div></div>
    <div class="profile-block"><h3>Languages</h3><div class="tag-list">${Object.entries(p.languages || {}).map(([k,v]) => `<span class="badge info">${escapeHtml(k)} ${escapeHtml(v)}</span>`).join('')}</div></div>
    <div class="profile-block"><h3>Skills</h3><div class="tag-list">${(p.skills || []).map(x => `<span class="badge neutral">${escapeHtml(x)}</span>`).join('')}</div></div>
    <div class="profile-block"><h3>Target seniority</h3><div class="tag-list">${(p.seniority?.allowed || []).map(x => `<span class="badge good">${escapeHtml(x)}</span>`).join('')}</div></div>
    <div class="profile-block"><h3>Education</h3>${(p.education || []).map(e => `<div class="detail-item"><span>${escapeHtml(e.degree_level || '')}</span><strong>${escapeHtml(e.field || '')}</strong><div class="helper">${escapeHtml(e.institution || '')}</div></div>`).join('')}</div>`;

  const s = data.settings;
  const w = s.matching?.overall_weights || {};
  document.getElementById('settingsSummary').innerHTML = `<div class="rule-list">
    <div class="rule"><span>Shortlist threshold</span><strong>${s.matching?.shortlist_threshold ?? 65}/100</strong></div>
    <div class="rule"><span>Freshness window</span><strong>${s.collection?.stale_after_days ?? 45} days</strong></div>
    <div class="rule"><span>Dry-run maximum</span><strong>${s.collection?.max_jobs_per_dry_run ?? 100}</strong></div>
    <div class="rule"><span>Technical weight</span><strong>${Math.round((w.technical || 0) * 100)}%</strong></div>
    <div class="rule"><span>Eligibility weight</span><strong>${Math.round((w.eligibility || 0) * 100)}%</strong></div>
    <div class="rule"><span>Location weight</span><strong>${Math.round((w.location || 0) * 100)}%</strong></div>
    <div class="rule"><span>Experience weight</span><strong>${Math.round((w.experience || 0) * 100)}%</strong></div>
    <div class="rule"><span>SQLite</span><strong>${escapeHtml(s.storage?.sqlite_path || 'data/jobs.db')}</strong></div>
  </div>`;
}

function populateStatusFilter() {
  const el = document.getElementById('jobsStatus');
  const current = el.value;
  el.innerHTML = '<option value="all">All statuses</option>' + state.statuses.map(s => `<option value="${s}">${prettyStatus(s)}</option>`).join('');
  el.value = [...el.options].some(o => o.value === current) ? current : 'all';
}

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
  state.jobsDebounce = setTimeout(() => { state.jobsPage = 1; loadJobs().catch(err => showToast(err.message, true)); }, 250);
}

async function bootstrap() {
  try {
    const health = await api('/api/health');
    document.getElementById('connectionDot').classList.add('online');
    document.getElementById('connectionText').textContent = 'Local API online';
    document.getElementById('appVersion').textContent = `v${health.version}`;
    const profile = await api('/api/profile');
    state.profile = profile.profile;
    state.settings = profile.settings;
    state.statuses = profile.application_statuses || [];
    populateStatusFilter();
    await loadOverview();
    await loadSources();
    const run = await api('/api/run/status');
    renderRunState(run);
    if (run.status === 'running') beginRunPolling();
  } catch (err) {
    document.getElementById('connectionDot').classList.add('offline');
    document.getElementById('connectionText').textContent = 'API offline';
    showToast(err.message, true);
  }
}

document.querySelectorAll('.nav-item').forEach(el => el.addEventListener('click', () => setView(el.dataset.view)));
document.querySelectorAll('[data-go]').forEach(el => el.addEventListener('click', () => setView(el.dataset.go)));
document.getElementById('refreshButton').addEventListener('click', () => refreshCurrentView().catch(err => showToast(err.message, true)));
document.getElementById('runButton').addEventListener('click', startCollection);
document.getElementById('runButtonPipeline').addEventListener('click', startCollection);
document.getElementById('closeJobModal').addEventListener('click', () => document.getElementById('jobModal').classList.add('hidden'));
document.getElementById('jobModal').addEventListener('click', e => { if (e.target.id === 'jobModal') e.currentTarget.classList.add('hidden'); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('jobModal').classList.add('hidden'); });

document.getElementById('jobsSearch').addEventListener('input', scheduleJobsReload);
['jobsMinScore','jobsSource','jobsStatus','jobsRemote','jobsFreshness','jobsSort'].forEach(id => document.getElementById(id).addEventListener('change', scheduleJobsReload));
document.getElementById('jobsPrev').addEventListener('click', () => { if (state.jobsPage > 1) { state.jobsPage--; loadJobs(); } });
document.getElementById('jobsNext').addEventListener('click', () => { if (state.jobsPage < state.jobsPages) { state.jobsPage++; loadJobs(); } });

// Make buttons used as rows look like ordinary blocks while remaining keyboard accessible.
const style = document.createElement('style');
style.textContent = `.reset-button{width:100%;border:0;background:transparent;color:inherit;padding-left:0;padding-right:0;font:inherit}.align-left{text-align:left}`;
document.head.appendChild(style);

bootstrap();
