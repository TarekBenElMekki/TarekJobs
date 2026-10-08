"use strict";

const API_BASE = "http://127.0.0.1:8765";
const SOURCE = "chrome_sidepanel_v1";
const elements = Object.fromEntries(
  [...document.querySelectorAll("[id]")].map(element => [element.id, element])
);
let mode = "review";
let transitionMap = {};
let activeFollowupStates = new Set();
const followupLabels = {
  awaiting_response: "AWAITING RESPONSE", assessment: "ASSESSMENT",
  interview: "INTERVIEW", technical_test: "TECHNICAL TEST",
  hr_interview: "HR INTERVIEW", offer: "OFFER", hired: "HIRED",
  rejected: "REJECTED", withdrawn: "WITHDRAWN",
};
let queue = [];
let index = 0;
let busy = false;
let queueReady = false;
const applicationLinks = new Map();
let linkRequestVersion = 0;
let linkLoading = false;

let sessionInfo = null;
let sessionWarning = "";
let sessionEnsurePending = null;

function renderSession() {
  const indicator = elements["session-indicator"];
  indicator.classList.toggle("warning", Boolean(sessionWarning));
  if (sessionWarning) { indicator.textContent = sessionWarning; return; }
  if (!sessionInfo?.started_at) { indicator.textContent = "SESSION CONNECTING"; return; }
  const elapsed = Math.max(0, Math.floor((Date.now() - Date.parse(sessionInfo.started_at)) / 1000));
  const hours = Math.floor(elapsed / 3600);
  const minutes = Math.floor(elapsed / 60) % 60;
  const seconds = elapsed % 60;
  indicator.textContent = `SESSION ACTIVE · ${[hours, minutes, seconds].map(n => String(n).padStart(2, "0")).join(":")}`;
}

async function ensureWorkSession() {
  if (sessionEnsurePending) return sessionEnsurePending;
  sessionEnsurePending = (async () => {
    try {
      let timer;
      let response;
      try {
        response = await Promise.race([
          chrome.runtime.sendMessage({ type: "ttea.ensureSession" }),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Session unavailable")), 7000); }),
        ]);
      } finally { clearTimeout(timer); }
      sessionInfo = response?.session || null;
      sessionWarning = response?.warning || (sessionInfo ? "" : "Session analytics unavailable.");
    } catch {
      sessionInfo = null;
      sessionWarning = "Session analytics unavailable; job workflow remains available.";
    }
    renderSession();
    return sessionInfo;
  })();
  try { return await sessionEnsurePending; } finally { sessionEnsurePending = null; }
}
class ApiError extends Error {
  constructor(message, offline = false) {
    super(message);
    this.offline = offline;
  }
}

async function request(path, options = {}) {
  const headers = options.body ? { "Content-Type": "application/json" } : {};
  if (options.method && !["GET", "HEAD"].includes(options.method.toUpperCase())) {
    const session = await ensureWorkSession();
    if (session) headers["X-TTEA-Session-Token"] = session.session_token;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = "";
      try {
        const body = await response.json();
        if (typeof body.detail === "string") detail = `: ${body.detail}`;
      } catch { /* An HTTP error need not contain JSON. */ }
      throw new ApiError(`TTEA request failed (${response.status})${detail}`);
    }
    try {
      const body = await response.json();
      elements.connection.textContent = "TTEA CONNECTED";
      return body;
    } catch {
      throw new ApiError("TTEA returned an invalid response.");
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Cannot reach TTEA. Start the local server and retry. If an action timed out, it may have been saved; retry the same action or refresh the queue.", true);
  } finally {
    clearTimeout(timer);
  }
}

function showError(error) {
  elements.error.textContent = error.message || "The action failed. Please retry.";
  elements.error.hidden = false;
  if (error.offline) elements.connection.textContent = "TTEA OFFLINE";
}

function updateButtons() {
  for (const button of document.querySelectorAll("button")) button.disabled = busy;
  elements.previous.disabled = busy || index <= 0;
  elements.next.disabled = busy || index >= queue.length - 1;
  elements.open.disabled = busy || !queue[index];
  elements["save-link"].disabled = busy || linkLoading || !queueReady || !queue[index];
  elements.card.setAttribute("aria-busy", String(busy));
}

async function perform(operation) {
  if (busy) return;
  busy = true;
  elements.error.hidden = true;
  updateButtons();
  try {
    await operation();
  } catch (error) {
    showError(error);
  } finally {
    busy = false;
    updateButtons();
  }
}

function showApplicationLink(url) {
  elements["application-link-value"].textContent = url ? url.replace(/^https?:\/\//i, "") : "Not saved";
  elements["application-link-value"].title = url || "";
  elements["save-link"].textContent = url ? "REPLACE WITH CURRENT PAGE" : "SAVE CURRENT PAGE";
}

async function loadApplicationLink(job) {
  const version = ++linkRequestVersion;
  linkLoading = false;
  if (applicationLinks.has(job.id)) {
    showApplicationLink(applicationLinks.get(job.id));
    return;
  }
  linkLoading = true;
  elements["application-link-value"].textContent = "Loading saved link…";
  elements["application-link-value"].title = "";
  elements["save-link"].textContent = "SAVE CURRENT PAGE";
  updateButtons();
  try {
    const bundle = await request(`/api/jobs/${job.id}/application`);
    if (!bundle || !("application" in bundle)) throw new ApiError("Invalid application response.");
    const url = bundle.application?.application_url ?? null;
    if (url !== null && typeof url !== "string") throw new ApiError("Invalid application link response.");
    if (version !== linkRequestVersion) return;
    applicationLinks.set(job.id, url);
    showApplicationLink(url);
  } catch (error) {
    if (version !== linkRequestVersion) return;
    elements["application-link-value"].textContent = "Saved link unavailable. Refresh to retry, or save the current page.";
    showError(error);
  } finally {
    if (version === linkRequestVersion) {
      linkLoading = false;
      updateButtons();
    }
  }
}

async function saveApplicationLink() {
  const job = queue[index];
  if (!queueReady || !job || linkLoading) return;
  let url;
  try {
    const currentWindow = await chrome.windows.getCurrent();
    const [tab] = await chrome.tabs.query({ active: true, windowId: currentWindow.id });
    // This explicit button action is the only place that captures a tab URL.
    url = tab?.url;
    if (!url) throw new Error("Missing tab URL");
  } catch {
    throw new Error("Could not read the current tab URL. Reload the extension to grant its tabs permission, select a browser tab, and retry.");
  }
  const bundle = await request(`/api/jobs/${job.id}/application/link`, {
    method: "PATCH",
    body: JSON.stringify({ url, source: SOURCE }),
  });
  const savedUrl = bundle?.application?.application_url;
  if (typeof savedUrl !== "string" || !savedUrl) throw new ApiError("TTEA did not return the saved application link. Refresh to reload it.");
  applicationLinks.set(job.id, savedUrl);
  showApplicationLink(savedUrl);
  if (mode === "followup") {
    job.application_url = savedUrl;
    job.last_activity_at = bundle.application.last_activity_at;
    render();
  }
  // Saving a link never changes queue position or review/application status.
}
function renderFollowupActions(job) {
  elements["followup-actions"].replaceChildren();
  const allowed = transitionMap[job.current_state] || [];
  for (const state of allowed) {
    if (!Object.hasOwn(followupLabels, state)) continue;
    const button = document.createElement("button");
    button.textContent = followupLabels[state];
    button.addEventListener("click", () => perform(() => setFollowupStatus(state)));
    elements["followup-actions"].append(button);
  }
}

async function setFollowupStatus(state) {
  const job = queue[index];
  if (mode !== "followup" || !queueReady || !job) return;
  if (!(transitionMap[job.current_state] || []).includes(state) || !Object.hasOwn(followupLabels, state)) {
    throw new Error("This transition is unavailable. Refresh the queue.");
  }
  const bundle = await request(`/api/jobs/${job.id}/application/transition`, {
    method: "POST",
    body: JSON.stringify({ state, source: SOURCE, metadata: { source: SOURCE, mode: "followup" } }),
  });
  const application = bundle?.application;
  if (!application || application.job_id !== job.id || typeof application.current_state !== "string") {
    throw new ApiError("Invalid application response. Refresh to reload the saved status.");
  }
  if (activeFollowupStates.has(application.current_state)) {
    const jobId = job.id;
    Object.assign(job, application, { id: jobId });
    applicationLinks.set(job.id, application.application_url || null);
  } else {
    queue.splice(index, 1);
    index = Math.min(index, Math.max(0, queue.length - 1));
  }
  render();
}

function render() {
  const job = queue[index];
  const followup = mode === "followup";
  elements["review-actions"].hidden = followup;
  elements["followup-actions"].hidden = !followup;
  elements["followup-details"].hidden = !followup;
  elements.position.textContent = queue.length ? `${index + 1} / ${queue.length}` : "0 / 0";
  elements.card.hidden = !queueReady || !job;
  elements["queue-state"].hidden = queueReady && Boolean(job);
  if (!job && queueReady) {
    linkRequestVersion += 1;
    linkLoading = false;
    elements["state-title"].textContent = "QUEUE COMPLETE";
    elements["state-detail"].textContent = "No jobs remain in this queue. Refresh the queue to check for more.";
    elements.retry.hidden = true;
  }
  if (job) {
    elements.title.textContent = job.title || "Untitled job";
    elements.company.textContent = job.company || "Company not specified";
    elements.location.textContent = job.location || "Location not specified";
    const score = job.score?.value;
    elements.score.textContent = score == null ? "SCORE —" : `SCORE ${score}`;
    elements.verdict.textContent = String(job.verdict || "Unknown verdict").replaceAll("_", " ").toUpperCase();
    const sources = [...new Set((job.sources || []).map(source => source.name).filter(Boolean))];
    elements.source.textContent = [job.opportunity_type, sources.join(", ")].filter(Boolean).join(" · ");
    elements.posted.textContent = job.posted_at ? `Posted ${String(job.posted_at).slice(0, 10)}` : "";
    elements.matches.replaceChildren();
    const strengths = job.ttea_assessment?.strengths;
    for (const strength of (Array.isArray(strengths) ? strengths : []).filter(item => typeof item === "string").slice(0, 2)) {
      const item = document.createElement("li");
      item.textContent = strength.length > 180 ? `${strength.slice(0, 177)}…` : strength;
      elements.matches.append(item);
    }
    elements.matches.hidden = !elements.matches.childElementCount;
    if (followup) {
      linkRequestVersion += 1;
      linkLoading = false;
      showApplicationLink(job.application_url);
      elements["current-status"].textContent = String(job.current_state).replaceAll("_", " ").toUpperCase();
      elements.submitted.textContent = job.submitted_at ? `Submitted ${String(job.submitted_at).slice(0, 10)}` : "Submission date unknown";
      renderFollowupActions(job);
    } else {
      void loadApplicationLink(job);
    }
    elements.open.textContent = followup && job.application_url ? "OPEN APPLICATION" : "OPEN JOB";
  }
  updateButtons();
}

async function loadQueue() {
  queueReady = false;
  linkRequestVersion += 1;
  linkLoading = false;
  applicationLinks.clear();
  elements.card.hidden = true;
  elements["queue-state"].hidden = false;
  elements["state-title"].textContent = "LOADING QUEUE";
  elements["state-detail"].textContent = mode === "followup" ? "Fetching active applications…" : "Fetching all reviewable jobs…";
  elements.retry.hidden = true;
  elements.position.textContent = "— / —";
  const snapshot = [];
  const seen = new Set();
  try {
    if (mode === "followup") {
      const [body, contract] = await Promise.all([
        request("/api/application-assistant/followups"),
        request("/api/application-assistant/states"),
      ]);
      if (!Array.isArray(body?.items) || !Array.isArray(body.active_states) ||
          !contract?.transitions || typeof contract.transitions !== "object" ||
          body.items.some(job => !Number.isInteger(job.id) || typeof job.current_state !== "string" ||
            !Array.isArray(contract.transitions[job.current_state]))) {
        throw new ApiError("Invalid follow-up queue or lifecycle contract.");
      }
      transitionMap = contract.transitions;
      activeFollowupStates = new Set(body.active_states);
      queue = body.items;
      index = 0;
      queueReady = true;
      render();
      return;
    }
    let pages = 1;
    for (let page = 1; page <= pages; page += 1) {
      const query = new URLSearchParams({
        review: "unreviewed", status: "not_started", verdict: "reviewable",
        freshness: "live", page_size: "100", page: String(page), sort: "score",
      });
      const body = await request(`/api/jobs?${query}`);
      if (!Array.isArray(body.items) || !Number.isInteger(body.pages) || body.pages < 1) {
        throw new ApiError("TTEA returned an invalid queue response.");
      }
      pages = body.pages;
      for (const job of body.items) {
        if (!Number.isInteger(job.id)) throw new ApiError("TTEA returned a job without a valid ID.");
        if (!seen.has(job.id)) {
          seen.add(job.id);
          snapshot.push(job);
        }
      }
      elements["state-detail"].textContent = `Loaded page ${page} / ${pages}…`;
    }
    queue = snapshot;
    index = 0;
    queueReady = true;
    render();
  } catch (error) {
    elements["state-title"].textContent = error.offline ? "TTEA OFFLINE" : "QUEUE LOAD FAILED";
    elements["state-detail"].textContent = error.offline ? "Start the local TTEA server and retry." : "Check the inline error and retry.";
    elements.retry.hidden = false;
    throw error;
  }
}

async function setStatus(action) {
  const job = queue[index];
  if (!job || !queueReady || mode !== "review") return;
  if (["dismissed", "shortlisted", "not_eligible"].includes(action)) {
    await request(`/api/jobs/${job.id}`, {
      method: "PATCH", body: JSON.stringify({ review_status: action }),
    });
  } else {
    const historical = action === "already_applied";
    // The normalized transition endpoint ensures the application transactionally.
    await request(`/api/jobs/${job.id}/application/transition`, {
      method: "POST",
      body: JSON.stringify({
        state: historical ? "applied" : action,
        source: SOURCE,
        metadata: {
          source: SOURCE,
          ...(historical ? { historical_application: true, application_date_unknown: true } : {}),
        },
      }),
    });
  }
  queue.splice(index, 1);
  // If the final card was completed first, return to the previous remaining card.
  index = Math.min(index, Math.max(0, queue.length - 1));
  render();
}

for (const nextMode of ["review", "followup"]) {
  elements[`mode-${nextMode}`].addEventListener("click", () => perform(async () => {
    mode = nextMode;
    for (const value of ["review", "followup"]) {
      elements[`mode-${value}`].setAttribute("aria-pressed", String(value === mode));
    }
    await loadQueue();
  }));
}

elements["save-link"].addEventListener("click", () => perform(saveApplicationLink));

for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => perform(() => setStatus(button.dataset.action)));
}
for (const id of ["refresh", "retry"]) {
  elements[id].addEventListener("click", () => perform(loadQueue));
}
for (const [id, delta] of [["previous", -1], ["next", 1]]) {
  elements[id].addEventListener("click", () => {
    if (busy || !queueReady) return;
    index = Math.max(0, Math.min(queue.length - 1, index + delta));
    elements.error.hidden = true;
    render();
  });
}
elements.open.addEventListener("click", () => perform(async () => {
  const job = queue[index];
  if (!job) return;
  let url;
  const targetUrl = mode === "followup" && job.application_url ? job.application_url : job.canonical_url;
  try { url = new URL(targetUrl); } catch { throw new Error("This card has no valid destination URL."); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw new Error("The destination must be an HTTP or HTTPS URL without credentials.");
  }
  try {
    const currentWindow = await chrome.windows.getCurrent();
    const [tab] = await chrome.tabs.query({ active: true, windowId: currentWindow.id });
    if (!tab || !Number.isInteger(tab.id)) throw new Error("No active tab");
    // Only the tab ID is used; no destination-page or current-URL inspection.
    await chrome.tabs.update(tab.id, { url: url.href });
  } catch {
    throw new Error("Could not open this job in the current tab. Select a browser tab and retry.");
  }
}));

void ensureWorkSession();
setInterval(renderSession, 1000); // Local display only; no per-second networking.
setInterval(() => { void ensureWorkSession(); }, 60000);
perform(loadQueue);
