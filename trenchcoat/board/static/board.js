/* Trenchcoat Detective Board — vanilla JS actions */

function toast(msg, isError) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' error' : '');
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

async function api(path, method = 'POST', body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== null) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  let data = {};
  try { data = await resp.json(); } catch (e) { /* empty */ }
  if (!resp.ok) {
    const msg = data.error || data.violations?.join('; ') || data.detail || `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

/* ── autopilot toggle ── */
document.getElementById('autopilot-toggle')?.addEventListener('change', async (e) => {
  const enabled = e.target.checked;
  try {
    await api('/api/autopilot', 'POST', { enabled });
    document.getElementById('autopilot-label').textContent = enabled ? 'AUTOPILOT' : 'CO-PILOT';
    toast(enabled ? 'Autopilot ON — guarded drafts will auto-submit.' : 'Co-Pilot mode — every submission needs your approval.');
  } catch (err) {
    e.target.checked = !enabled;
    toast('Toggle failed: ' + err.message, true);
  }
});

/* ── operations ── */
document.getElementById('btn-scout')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-scout');
  btn.disabled = true; btn.textContent = 'Scouting…';
  try {
    await api('/api/scout', 'POST', {});
    toast('Scout started. Polling for completion…');
    pollRunStatus();
  } catch (err) {
    btn.disabled = false; btn.textContent = '▶ Run scout';
    toast('Scout failed: ' + err.message, true);
  }
});

function pollRunStatus() {
  const iv = setInterval(async () => {
    try {
      const state = await api('/api/state', 'GET');
      const el = document.getElementById('run-status');
      if (state.run_state.running) {
        if (el) el.textContent = 'run in progress…';
      } else {
        clearInterval(iv);
        const r = state.run_state.last_report || {};
        if (el) el.textContent = `last run: ${r.candidates_created ?? 0} candidates, ${r.auto_submitted ?? 0} submitted`;
        toast(`Scout done: ${r.candidates_created ?? 0} candidates, ${r.markets_researched ?? 0} markets researched.`);
        setTimeout(() => location.reload(), 1200);
      }
    } catch (e) { clearInterval(iv); }
  }, 2500);
}

document.getElementById('btn-poll')?.addEventListener('click', async () => {
  try {
    const res = await api('/api/poll', 'POST', {});
    toast(`Receipts refreshed: ${res.updated} updated, ${res.pending} still pending.`);
    setTimeout(() => location.reload(), 900);
  } catch (err) { toast('Poll failed: ' + err.message, true); }
});

document.getElementById('btn-demo')?.addEventListener('click', async () => {
  try {
    const res = await api('/api/seed-demo', 'POST', {});
    toast(`Demo seeded: ${res.candidates} candidates, ${res.markets} markets.`);
    setTimeout(() => location.reload(), 900);
  } catch (err) { toast('Demo seed failed: ' + err.message, true); }
});

/* ── candidate actions ── */
async function approveCandidate(id) {
  if (!confirm('Approve and submit this evidence? Guardrails will re-check at send time.')) return;
  try {
    const res = await api(`/api/candidates/${id}/approve`, 'POST', {});
    toast(res.replayed ? 'Already submitted (idempotent replay).' : `Submitted: ${res.submission_id} (${res.mode || ''})`);
    setTimeout(() => location.reload(), 1000);
  } catch (err) { toast('Submit blocked: ' + err.message, true); }
}

async function discardCandidate(id) {
  if (!confirm('Discard this draft? It will not be submitted.')) return;
  try {
    await api(`/api/candidates/${id}/discard`, 'POST', {});
    toast('Discarded.');
    setTimeout(() => location.reload(), 700);
  } catch (err) { toast('Discard failed: ' + err.message, true); }
}

/* ── edit modal ── */
let editingId = null;
function openEdit(id) {
  const c = (typeof CANDIDATES !== 'undefined') ? CANDIDATES.find(x => x.id === id) : null;
  if (!c) { toast('Candidate data not found', true); return; }
  editingId = id;
  document.getElementById('edit-id').textContent = '#' + id;
  document.getElementById('edit-stance').value = c.stance;
  document.getElementById('edit-claim').value = c.claim;
  document.getElementById('edit-note').value = c.note;
  document.getElementById('edit-links').value = (c.links || []).join('\n');
  document.getElementById('edit-modal').hidden = false;
}
function closeEdit() {
  document.getElementById('edit-modal').hidden = true;
  editingId = null;
}
async function saveEdit(andSubmit) {
  if (!editingId) return;
  const links = document.getElementById('edit-links').value.split('\n').map(s => s.trim()).filter(Boolean);
  const payload = {
    stance: document.getElementById('edit-stance').value,
    claim: document.getElementById('edit-claim').value,
    note: document.getElementById('edit-note').value,
    links,
  };
  try {
    const res = await api(`/api/candidates/${editingId}/edit`, 'POST', payload);
    if (!res.ok) { toast('Saved but blocked: ' + (res.violations || []).join('; '), true); closeEdit(); setTimeout(() => location.reload(), 1500); return; }
    if (andSubmit) {
      const sub = await api(`/api/candidates/${editingId}/approve`, 'POST', {});
      toast(`Submitted: ${sub.submission_id}`);
    } else {
      toast('Draft saved. New content → new idempotency key.');
    }
    closeEdit();
    setTimeout(() => location.reload(), 1000);
  } catch (err) { toast('Edit failed: ' + err.message, true); }
}
document.getElementById('edit-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'edit-modal') closeEdit();
});

/* ── feedback ── */
async function addFeedback() {
  const payload = {
    area: document.getElementById('fb-area').value,
    title: document.getElementById('fb-title').value,
    text: document.getElementById('fb-text').value,
    market_id: document.getElementById('fb-market').value || null,
    submission_id: document.getElementById('fb-submission').value || null,
  };
  if (!payload.text.trim()) { toast('Text is required.', true); return; }
  try {
    await api('/api/feedback', 'POST', payload);
    toast('Quirk logged.');
    setTimeout(() => location.reload(), 700);
  } catch (err) { toast('Log failed: ' + err.message, true); }
}
async function sendFeedback(id) {
  try {
    const res = await api(`/api/feedback/${id}/send`, 'POST', {});
    toast(res.mode === 'live' ? `Sent to Delphi: ${res.remote_id}` : `Captured to outbox: ${res.remote_id}`);
    setTimeout(() => location.reload(), 900);
  } catch (err) { toast('Send failed: ' + err.message, true); }
}

/* ── terms ── */
async function acceptTerms(version) {
  const confirmBox = document.getElementById('terms-confirm');
  if (!confirmBox?.checked) { toast('Check the confirmation box first — explicit consent required.', true); return; }
  try {
    await api('/api/terms/accept', 'POST', { version, confirm: true });
    toast('Terms accepted.');
    setTimeout(() => location.reload(), 800);
  } catch (err) { toast('Accept failed: ' + err.message, true); }
}
