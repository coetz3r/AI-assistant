// LUNA monitor — AI Activity page.
// Relies on monitor-common.js (loaded first) for the websocket connection
// and shared formatting/sparkline helpers.

const latencyHistory = [];

function backendPillHtml(backend) {
  const cls = backend === 'local' ? 'local' : (backend === 'groq' ? 'groq' : (backend ? 'groq_failed' : 'unknown'));
  return '<span class="backend-pill ' + cls + '">' + (backend || 'unknown') + '</span>';
}

function renderRecentTurns(turns) {
  const body = document.getElementById('turnsBody');
  if (!turns || !turns.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty-note">Waiting for the first turn…</td></tr>';
    return;
  }
  body.innerHTML = turns.map(t => (
    '<tr>' +
      '<td class="muted">' + fmtTime(t.timestamp) + '</td>' +
      '<td>' + escapeHtml(t.user_snippet || '') + '</td>' +
      '<td>' + backendPillHtml(t.backend) + '</td>' +
      '<td class="num">' + fmtMs(t.latency_ms) + '</td>' +
    '</tr>'
  )).join('');
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function renderStageTimings(turn) {
  const stages = [
    { key: 'vad_ms', fillId: 'stageVadFill', numId: 'stageVadNum' },
    { key: 'stt_ms', fillId: 'stageSttFill', numId: 'stageSttNum' },
    { key: 'llm_ms', fillId: 'stageLlmFill', numId: 'stageLlmNum' },
    { key: 'tts_ms', fillId: 'stageTtsFill', numId: 'stageTtsNum' },
  ];

  const metaEl = document.getElementById('stageTurnMeta');
  const snippetEl = document.getElementById('stageUserSnippet');

  if (!turn) {
    stages.forEach(s => {
      document.getElementById(s.fillId).style.width = '0%';
      document.getElementById(s.numId).textContent = '—';
    });
    metaEl.textContent = 'last turn —';
    snippetEl.textContent = '';
    return;
  }

  // Bars are scaled relative to the slowest stage in this turn, so the
  // stack always reads clearly regardless of absolute latency.
  const maxMs = Math.max(1, ...stages.map(s => turn[s.key] || 0));

  stages.forEach(s => {
    const val = turn[s.key];
    const fillEl = document.getElementById(s.fillId);
    const numEl = document.getElementById(s.numId);
    if (val == null) {
      fillEl.style.width = '0%';
      numEl.textContent = '—';
    } else {
      fillEl.style.width = Math.max(2, (val / maxMs) * 100) + '%';
      numEl.textContent = fmtMs(val);
    }
  });

  metaEl.textContent = fmtTime(turn.timestamp) + ' · ' + (turn.backend || 'unknown') + ' · ' + (turn.outcome || '');
  snippetEl.textContent = turn.user_text ? ('"' + turn.user_text + '"') : '';
}

function applySnapshot(data) {
  const eng = data.engine || {};

  renderStageTimings(data.latest_turn);

  document.getElementById('engTotal').textContent = eng.total_requests ?? 0;
  document.getElementById('engLatency').textContent = fmtMs(eng.last_latency_ms);
  document.getElementById('engAvgLatency').textContent = fmtMs(eng.avg_latency_ms);

  const backendEl = document.getElementById('engBackend');
  backendEl.textContent = eng.last_backend || '—';
  backendEl.style.color = eng.last_backend === 'local' ? 'var(--local)'
    : (eng.last_backend === 'groq' ? 'var(--cloud)' : 'var(--crit)');

  document.getElementById('engineActive').textContent = (data.active_connections || 0) + ' active connections';

  const total = (eng.local_count || 0) + (eng.cloud_count || 0);
  const localPct = total ? (eng.local_count / total) * 100 : 0;
  const cloudPct = total ? (eng.cloud_count / total) * 100 : 0;
  document.getElementById('splitLocal').style.width = localPct + '%';
  document.getElementById('splitCloud').style.width = cloudPct + '%';
  document.getElementById('splitLocalNum').textContent = eng.local_count || 0;
  document.getElementById('splitCloudNum').textContent = eng.cloud_count || 0;

  if (eng.last_latency_ms != null) {
    pushHistory(latencyHistory, eng.last_latency_ms);
    drawSparkline(document.getElementById('latSpark'), latencyHistory, { color: '#9C7DF0', minMax: 200 });
  }

  document.getElementById('memFacts').textContent = eng.total_facts ?? '—';
  document.getElementById('memConvos').textContent = eng.total_conversations ?? '—';
  document.getElementById('memSession').textContent = eng.session_id || '—';
  document.getElementById('memSaved').textContent = fmtTime(eng.last_saved);

  renderRecentTurns(eng.recent_turns);
}

connectMonitorSocket(applySnapshot);
