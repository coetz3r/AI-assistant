// LUNA monitor — History page.
// Unlike the other monitor pages this one isn't driven by the live
// /ws/monitor feed — it reads the persisted turn log via /api/history so
// entries survive a server restart and can be reviewed after the fact.

const PAGE_SIZE = 100;

let allEntries = [];   // everything fetched so far, newest first
let oldestTimestamp = null;
let hasMore = true;

function backendPillHtml(backend) {
  const cls = backend === 'local' ? 'local' : (backend === 'groq' ? 'groq' : 'unknown');
  return '<span class="backend-pill ' + cls + '">' + (backend || 'unknown') + '</span>';
}

function outcomePillHtml(outcome) {
  return '<span class="backend-pill outcome_' + (outcome || 'unknown') + '">' + (outcome || 'unknown') + '</span>';
}

function stageCell(ms) {
  return ms == null ? '<span class="muted">—</span>' : fmtMs(ms);
}

function matchesFilters(entry, backend, outcome) {
  if (backend && entry.backend !== backend) return false;
  if (outcome && entry.outcome !== outcome) return false;
  return true;
}

function renderTable() {
  const backend = document.getElementById('historyBackendFilter').value;
  const outcome = document.getElementById('historyOutcomeFilter').value;

  const filtered = allEntries.filter(e => matchesFilters(e, backend, outcome));
  const body = document.getElementById('historyBody');

  if (!filtered.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty-note">No turns match.</td></tr>';
    return;
  }

  body.innerHTML = filtered.map(t => (
    '<tr class="history-row">' +
      '<td class="muted">' + fmtTime(t.timestamp) + '</td>' +
      '<td class="muted">' + (t.source === 'text' ? 'text' : 'voice') + '</td>' +
      '<td>' + backendPillHtml(t.backend) + '</td>' +
      '<td>' + outcomePillHtml(t.outcome) + '</td>' +
      '<td class="num">' + stageCell(t.vad_ms) + '</td>' +
      '<td class="num">' + stageCell(t.stt_ms) + '</td>' +
      '<td class="num">' + stageCell(t.llm_ms) + '</td>' +
      '<td class="num">' + stageCell(t.tts_ms) + '</td>' +
      '<td class="num">' + stageCell(t.total_ms) + '</td>' +
    '</tr>'
  )).join('');
}

function updateMeta() {
  document.getElementById('historyMeta').textContent = allEntries.length + ' turns loaded';
  document.getElementById('loadMoreBtn').style.display = hasMore ? '' : 'none';
}

async function fetchHistory(before) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (before != null) params.set('before', String(before));

  const res = await fetch('/api/history?' + params.toString());
  if (!res.ok) throw new Error('History fetch failed: ' + res.status);
  return res.json();
}

async function loadInitial() {
  document.getElementById('historyMeta').textContent = 'loading…';
  try {
    const data = await fetchHistory(null);
    allEntries = data.entries || [];
    hasMore = (data.entries || []).length === PAGE_SIZE;
    oldestTimestamp = allEntries.length ? allEntries[allEntries.length - 1].timestamp : null;
  } catch (e) {
    document.getElementById('historyBody').innerHTML =
      '<tr><td colspan="9" class="empty-note">Couldn\'t load history — is the server running?</td></tr>';
    hasMore = false;
  }
  updateMeta();
  renderTable();
}

async function loadMore() {
  if (!hasMore || oldestTimestamp == null) return;
  try {
    const data = await fetchHistory(oldestTimestamp);
    const newEntries = data.entries || [];
    allEntries = allEntries.concat(newEntries);
    hasMore = newEntries.length === PAGE_SIZE;
    if (newEntries.length) {
      oldestTimestamp = newEntries[newEntries.length - 1].timestamp;
    }
  } catch (e) {
    hasMore = false;
  }
  updateMeta();
  renderTable();
}

document.getElementById('historyRefreshBtn').addEventListener('click', loadInitial);
document.getElementById('loadMoreBtn').addEventListener('click', loadMore);
document.getElementById('historyBackendFilter').addEventListener('change', renderTable);
document.getElementById('historyOutcomeFilter').addEventListener('change', renderTable);

loadInitial();
