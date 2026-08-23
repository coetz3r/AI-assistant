// LUNA monitor dashboard client.
// Connects to /ws/monitor, receives a JSON snapshot ~once a second,
// and drives the dials / bars / sparklines. No external chart lib —
// sparklines are drawn directly onto <canvas> to match the rest of
// LUNA's dependency-free front end.

const connDot = document.getElementById('connDot');
const connLabel = document.getElementById('connLabel');
const connTime = document.getElementById('connTime');

// ---- rolling history buffers for sparklines --------------------------
const HISTORY_LEN = 60; // ~60s at 1 snapshot/sec
const netUpHistory = [];
const netDownHistory = [];
const latencyHistory = [];

function pushHistory(arr, value) {
  arr.push(value);
  if (arr.length > HISTORY_LEN) arr.shift();
}

// ---- dial helper -------------------------------------------------------
function setDial(dialEl, valueEl, pct, unit) {
  const clamped = Math.max(0, Math.min(100, pct));
  dialEl.style.setProperty('--pct', clamped.toFixed(1));

  let color = 'var(--nominal)';
  if (clamped >= 90) color = 'var(--crit)';
  else if (clamped >= 70) color = 'var(--warn)';
  dialEl.style.setProperty('--dial-color', color);

  valueEl.innerHTML = Math.round(clamped) + '<span class="unit">' + (unit || '%') + '</span>';
}

// ---- sparkline drawer ---------------------------------------------------
function drawSparkline(canvas, series, opts) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  if (!series.length) return;
  const max = Math.max(opts.minMax || 1, ...series);

  ctx.strokeStyle = opts.color || '#4FD8E8';
  ctx.lineWidth = 2;
  ctx.beginPath();
  series.forEach((v, i) => {
    const x = (i / (HISTORY_LEN - 1)) * w;
    const y = h - (v / max) * (h - 8) - 4;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // soft fill under the line
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.globalAlpha = 0.08;
  ctx.fillStyle = opts.color || '#4FD8E8';
  ctx.fill();
  ctx.globalAlpha = 1;
}

// ---- per-core bars, built once then updated in place --------------------
let coreBarsBuilt = false;
function updateCoreBars(perCore) {
  const wrap = document.getElementById('cpuCores');
  if (!coreBarsBuilt) {
    wrap.innerHTML = perCore.map(() =>
      '<div class="core-bar"><div class="core-bar-fill"></div></div>'
    ).join('');
    coreBarsBuilt = true;
  }
  const fills = wrap.querySelectorAll('.core-bar-fill');
  perCore.forEach((pct, i) => {
    if (!fills[i]) return;
    fills[i].style.width = pct + '%';
    fills[i].style.background = pct >= 85 ? 'var(--crit)' : pct >= 60 ? 'var(--warn)' : 'var(--nominal)';
  });
}

// ---- apply one snapshot to the whole page --------------------------------
function applySnapshot(data) {
  connTime.textContent = new Date(data.timestamp * 1000).toLocaleTimeString();

  // CPU
  if (data.cpu.available) {
    setDial(document.getElementById('cpuDial'), document.getElementById('cpuValue'), data.cpu.percent_total);
    updateCoreBars(data.cpu.percent_per_core);
    document.getElementById('cpuFreq').textContent = data.cpu.freq_mhz ? data.cpu.freq_mhz + ' MHz' : '';
    document.getElementById('cpuLoad').textContent = 'load avg ' + data.cpu.load_avg.join(' / ');
  }

  // Memory
  if (data.memory.available) {
    setDial(document.getElementById('memDial'), document.getElementById('memValue'), data.memory.percent);
    document.getElementById('memTotal').textContent = (data.memory.total_mb / 1024).toFixed(1) + ' GB total';
    document.getElementById('ramBarFill').style.width = data.memory.percent + '%';
    document.getElementById('ramBarNum').textContent = data.memory.used_mb + ' MB';
    document.getElementById('swapBarFill').style.width = data.memory.swap_percent + '%';
    document.getElementById('swapBarNum').textContent = data.memory.swap_used_mb + ' MB';
  }

  // GPU
  const gpuNote = document.getElementById('gpuNote');
  if (data.gpu.available) {
    gpuNote.textContent = '';
    document.getElementById('gpuBody').style.opacity = 1;
    if (data.gpu.busy_percent !== null && data.gpu.busy_percent !== undefined) {
      setDial(document.getElementById('gpuDial'), document.getElementById('gpuValue'), data.gpu.busy_percent);
    }
    document.getElementById('gpuTemp').textContent = data.gpu.temp_c ? data.gpu.temp_c + ' °C' : '';
  } else {
    document.getElementById('gpuBody').style.opacity = 0.35;
    gpuNote.textContent = data.gpu.note || 'GPU telemetry unavailable on this system.';
  }

  // Network
  if (data.network.available) {
    document.getElementById('netUp').textContent = data.network.sent_kbps.toFixed(1) + ' KB/s';
    document.getElementById('netDown').textContent = data.network.recv_kbps.toFixed(1) + ' KB/s';
    pushHistory(netUpHistory, data.network.sent_kbps);
    pushHistory(netDownHistory, data.network.recv_kbps);
    drawSparkline(document.getElementById('netSpark'), netDownHistory, { color: '#4FD8E8', minMax: 20 });

    const wifi = data.network.wifi;
    document.getElementById('wifiSignal').textContent = (wifi && wifi.available)
      ? wifi.iface + ' · ' + wifi.signal_dbm + ' dBm'
      : 'no wireless interface detected';
  }

  // AI Engine
  const eng = data.engine || {};
  document.getElementById('engTotal').textContent = eng.total_requests ?? 0;
  document.getElementById('engLatency').textContent = eng.last_latency_ms != null ? Math.round(eng.last_latency_ms) + ' ms' : '—';
  document.getElementById('engAvgLatency').textContent = eng.avg_latency_ms != null ? Math.round(eng.avg_latency_ms) + ' ms' : '—';
  const backendEl = document.getElementById('engBackend');
  backendEl.textContent = eng.last_backend || '—';
  backendEl.style.color = eng.last_backend === 'local' ? 'var(--local)'
    : (eng.last_backend === 'groq' ? 'var(--cloud)' : 'var(--crit)');

  document.getElementById('engineActive').textContent = (data.active_connections || 0) + ' active';

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
}

// ---- WebSocket connection with auto-reconnect ---------------------------
function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(protocol + '//' + location.host + '/ws/monitor');

  ws.onopen = () => {
    connDot.classList.add('live');
    connLabel.textContent = 'live';
  };

  ws.onmessage = (event) => {
    try {
      applySnapshot(JSON.parse(event.data));
    } catch (e) {
      console.warn('Bad monitor snapshot:', e);
    }
  };

  ws.onclose = () => {
    connDot.classList.remove('live');
    connLabel.textContent = 'reconnecting…';
    setTimeout(connect, 2000);
  };

  ws.onerror = () => ws.close();
}

connect();
