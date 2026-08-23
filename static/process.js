// LUNA monitor — Process History page.
// Relies on monitor-common.js (loaded first) for the websocket connection
// and shared formatting/sparkline helpers.

const procCountHistory = [];

function renderProcTable(bodyId, rows, pctKey) {
  const body = document.getElementById(bodyId);
  if (!rows || !rows.length) {
    body.innerHTML = '<tr><td colspan="3" class="empty-note">No process data available.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(p => (
    '<tr>' +
      '<td class="muted">' + p.pid + '</td>' +
      '<td>' + p.name + '</td>' +
      '<td class="num">' + p[pctKey].toFixed(1) + '%</td>' +
    '</tr>'
  )).join('');
}

function applySnapshot(data) {
  const proc = data.processes;
  if (!proc || !proc.available) return;

  document.getElementById('procCount').textContent = proc.total_count + ' running';

  pushHistory(procCountHistory, proc.total_count);
  drawSparkline(document.getElementById('procSpark'), procCountHistory, { color: '#4FD8E8', minMax: 20 });

  renderProcTable('topCpuBody', proc.top_cpu, 'cpu_percent');
  renderProcTable('topMemBody', proc.top_memory, 'memory_percent');
}

connectMonitorSocket(applySnapshot);
