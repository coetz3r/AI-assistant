// LUNA monitor — Overview page.
// Relies on monitor-common.js (loaded first) for the websocket connection,
// dial/sparkline drawing, and formatting helpers.

const cpuHistory = [];
const memHistory = [];
const gpuHistory = [];

// ---- per-core bars, built once then updated in place --------------------
let coreBarsBuilt = false;
function updateCoreBars(perCore) {
  const wrap = document.getElementById('cpuCores');
  if (!coreBarsBuilt) {
    wrap.innerHTML = perCore.map((_, i) =>
      '<div class="core-bar-wrap"><span>C' + i + '</span><div class="core-bar"><div class="core-bar-fill"></div></div><span class="core-num"></span></div>'
    ).join('');
    coreBarsBuilt = true;
  }
  const rows = wrap.querySelectorAll('.core-bar-wrap');
  perCore.forEach((pct, i) => {
    if (!rows[i]) return;
    const fill = rows[i].querySelector('.core-bar-fill');
    const num = rows[i].querySelector('.core-num');
    fill.style.width = pct + '%';
    fill.style.background = pct >= 85 ? 'var(--crit)' : pct >= 60 ? 'var(--warn)' : 'var(--nominal)';
    num.textContent = Math.round(pct) + '%';
  });
}

function topProcLabel(list) {
  if (!list || !list.length) return '—';
  const p = list[0];
  return p.name + ' (' + p.cpu_percent + '%)';
}

function topMemProcLabel(list) {
  if (!list || !list.length) return '—';
  const p = list[0];
  return p.name + ' (' + p.memory_percent + '%)';
}

function applySnapshot(data) {
  // CPU
  if (data.cpu.available) {
    setDial(document.getElementById('cpuDial'), document.getElementById('cpuValue'), data.cpu.percent_total);
    updateCoreBars(data.cpu.percent_per_core);
    document.getElementById('cpuFreq').textContent = data.cpu.freq_mhz ? data.cpu.freq_mhz + ' MHz' : '';
    document.getElementById('cpuLoad').textContent = data.cpu.load_avg.join(' / ');
    pushHistory(cpuHistory, data.cpu.percent_total);
    drawSparkline(document.getElementById('cpuSpark'), cpuHistory, { color: '#4FD8E8', minMax: 100 });
  }

  // Memory
  if (data.memory.available) {
    setDial(document.getElementById('memDial'), document.getElementById('memValue'), data.memory.percent);
    document.getElementById('memTotal').textContent = (data.memory.total_mb / 1024).toFixed(1) + ' GB total';
    document.getElementById('ramBarFill').style.width = data.memory.percent + '%';
    document.getElementById('ramBarNum').textContent = data.memory.used_mb + ' MB';
    document.getElementById('swapBarFill').style.width = data.memory.swap_percent + '%';
    document.getElementById('swapBarNum').textContent = data.memory.swap_used_mb + ' MB';
    document.getElementById('memAvailable').textContent = (data.memory.total_mb - data.memory.used_mb) + ' MB';
    pushHistory(memHistory, data.memory.percent);
    drawSparkline(document.getElementById('memSpark'), memHistory, { color: '#9C7DF0', minMax: 100 });
  }

  // GPU
  const gpuNote = document.getElementById('gpuNote');
  if (data.gpu.available) {
    gpuNote.textContent = '';
    document.getElementById('gpuBody').style.opacity = 1;
    if (data.gpu.busy_percent !== null && data.gpu.busy_percent !== undefined) {
      setDial(document.getElementById('gpuDial'), document.getElementById('gpuValue'), data.gpu.busy_percent);
      pushHistory(gpuHistory, data.gpu.busy_percent);
      drawSparkline(document.getElementById('gpuSpark'), gpuHistory, { color: '#E8A23D', minMax: 100 });
    }
    document.getElementById('gpuTemp').textContent = data.gpu.temp_c ? data.gpu.temp_c + ' °C' : '';
  } else {
    document.getElementById('gpuBody').style.opacity = 0.35;
    gpuNote.textContent = data.gpu.note || 'GPU telemetry unavailable on this system.';
  }

  // Process footnotes on CPU / Memory panels
  if (data.processes && data.processes.available) {
    document.getElementById('cpuTopProc').textContent = topProcLabel(data.processes.top_cpu);
    document.getElementById('memTopProc').textContent = topMemProcLabel(data.processes.top_memory);
    document.getElementById('chipProc').textContent = data.processes.total_count + ' running';
  }

  // Link-out chips
  const eng = data.engine || {};
  document.getElementById('chipAi').textContent = (eng.total_requests || 0) + ' turns · ' + (eng.last_backend || '—');

  if (data.network && data.network.available) {
    document.getElementById('chipNet').textContent =
      data.network.sent_kbps.toFixed(0) + '↑ / ' + data.network.recv_kbps.toFixed(0) + '↓ KB/s';
  }
}

connectMonitorSocket(applySnapshot);
