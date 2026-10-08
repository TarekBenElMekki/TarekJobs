"use strict";

const API_BASE = "http://127.0.0.1:8765";
const HEARTBEAT = "ttea-session-heartbeat";
let lifecycle = Promise.resolve();
function serialized(operation) {
  const result = lifecycle.then(operation);
  lifecycle = result.catch(() => {});
  return result;
}

async function diagnostic(event) {
  try {
    const { session_diagnostics = [] } = await chrome.storage.local.get("session_diagnostics");
    session_diagnostics.push({ at: new Date().toISOString(), event });
    await chrome.storage.local.set({ session_diagnostics: session_diagnostics.slice(-100) });
  } catch { /* Diagnostics cannot disable the job workflow. */ }
}

async function sessionRequest(path, body = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${API_BASE}/api/application-assistant/sessions${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      credentials: "omit", cache: "no-store", signal: controller.signal,
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error("Session service unavailable");
    const data = await response.json();
    if (!data.session || !data.session.session_token) throw new Error("Invalid session response");
    return data.session;
  } finally { clearTimeout(timer); }
}

async function endSession() {
  const { work_session: session } = await chrome.storage.session.get("work_session");
  // Clear locally even if delivery fails. A subsequent session recovers the old
  // server row at last_seen_at, never at an invented shutdown timestamp.
  await chrome.storage.session.remove("work_session");
  await chrome.alarms.clear(HEARTBEAT);
  if (!session) return;
  try {
    await sessionRequest(`/${session.session_token}/end`, { reason: "last_window_closed" });
    await diagnostic("session_ended_last_window_closed");
  } catch { await diagnostic("session_end_undelivered_recovery_required"); }
}

async function ensureSession() {
  const windows = await chrome.windows.getAll({ windowTypes: ["normal"] });
  if (!windows.length) {
    await endSession();
    return { warning: "No normal browser window is open." };
  }
  let { client_id } = await chrome.storage.local.get("client_id");
  if (!client_id) {
    client_id = crypto.randomUUID();
    await chrome.storage.local.set({ client_id });
  }
  let { work_session: session } = await chrome.storage.session.get("work_session");
  if (!session) {
    let timezone = "UTC";
    try { timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch {}
    session = { session_token: crypto.randomUUID(), timezone, started_at: null };
    // Persist before networking to make retries and worker suspension idempotent.
    await chrome.storage.session.set({ work_session: session });
  }
  if (!await chrome.alarms.get(HEARTBEAT)) await chrome.alarms.create(HEARTBEAT, { periodInMinutes: 1 });
  if (session.started_at && !session.warning) return { session };
  try {
    const saved = await sessionRequest("/start", {
      client_id, session_token: session.session_token, timezone: session.timezone,
      extension_version: chrome.runtime.getManifest().version,
    });
    if (saved.ended_at) {
      await chrome.storage.session.remove("work_session");
      return ensureSession();
    }
    session = { session_token: saved.session_token, timezone: saved.timezone, started_at: saved.started_at };
    await chrome.storage.session.set({ work_session: session });
    await diagnostic("session_started_or_resumed");
    return { session };
  } catch {
    session.warning = "Session analytics unavailable; job workflow remains available.";
    await chrome.storage.session.set({ work_session: session });
    await diagnostic("session_start_unavailable");
    return { warning: session.warning };
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || message?.type !== "ttea.ensureSession") return false;
  serialized(ensureSession).then(respond, () => respond({ warning: "Session analytics unavailable." }));
  return true;
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name !== HEARTBEAT) return;
  void serialized(async () => {
    const { work_session: previous } = await chrome.storage.session.get("work_session");
    if (!previous) { await chrome.alarms.clear(HEARTBEAT); return; }
    const result = await ensureSession();
    if (!result.session) return;
    try {
      const saved = await sessionRequest(`/${result.session.session_token}/heartbeat`);
      if (saved.ended_at) {
        await chrome.storage.session.remove("work_session");
        await chrome.alarms.clear(HEARTBEAT);
      } else {
        await diagnostic("session_heartbeat_recorded");
      }
    } catch {
      await chrome.storage.session.set({ work_session: { ...result.session, warning: "Session heartbeat unavailable." } });
      await diagnostic("session_heartbeat_unavailable");
    }
  }).catch(() => diagnostic("session_lifecycle_unavailable"));
});

chrome.windows.onRemoved.addListener(() => {
  void serialized(async () => {
    const remaining = await chrome.windows.getAll({ windowTypes: ["normal"] });
    if (!remaining.length) await endSession();
  }).catch(() => diagnostic("session_close_check_unavailable"));
});

// No onSuspend guarantee: process kills are recovered on the next session start.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {
  void diagnostic("sidepanel_toolbar_setup_failed");
});
