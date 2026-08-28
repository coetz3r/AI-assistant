// LUNA monitor — shared helpers used by every dashboard page
// (monitor.html, ai.html, network.html, process.html).
//
// Each page includes this file, then calls connectMonitorSocket(fn) with
// its own render function — the feed and connection-status wiring are
// identical everywhere, only what each page does with the snapshot differs.

const HISTORY_LEN = 60; // ~60s at 1 snapshot/sec

function pushHistory(arr, value) {
  arr.push(value);
  if (arr.length > HISTORY_LEN) arr.shift();
}

// ---- dial helper -------------------------------------------------------
function setDial(dialEl, valueEl, pct, unit) {
  if (!dialEl || !valueEl) return;
  const clamped = Math.max(0, Math.min(100, pct));
  dialEl.style.setProperty('--pct', clamped.toFixed(1));

  let color = 'var(--nominal)';
  if (clamped >= 90) color = 'var(--crit)';
  else if (clamped >= 70) color = 'var(--warn)';
  dialEl.style.setProperty('--dial-color', color);

  valueEl.innerHTML = Math.round(clamped) + '<span class="unit">' + (unit || '%') + '</span>';
}

// ---- sparkline drawer ----------------------------------------------------
// Draws one or more series onto a canvas. `series` can be a flat array
// (single line) or an array of {data, color} for multiple overlaid lines.
function drawSparkline(canvas, series, opts) {
  if (!canvas) return;
  opts = opts || {};
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const lines = (Array.isArray(series) && series.length && typeof series[0] === 'object')
    ? series
    : [{ data: series || [], color: opts.color || '#4FD8E8' }];

  const allValues = lines.flatMap(l => l.data);
  if (!allValues.length) return;
  const max = Math.max(opts.minMax || 1, ...allValues);

  lines.forEach(line => {
    const data = line.data;
    if (!data.length) return;

    ctx.strokeStyle = line.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = (i / (HISTORY_LEN - 1)) * w;
      const y = h - (v / max) * (h - 8) - 4;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    if (!opts.noFill) {
      ctx.lineTo(w, h);
      ctx.lineTo(0, h);
      ctx.closePath();
      ctx.globalAlpha = 0.08;
      ctx.fillStyle = line.color;
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  });
}

// ---- websocket feed shared by every page ---------------------------------
function connectMonitorSocket(onSnapshot) {
  const connDot = document.getElementById('connDot');
  const connLabel = document.getElementById('connLabel');
  const connTime = document.getElementById('connTime');

  function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(protocol + '//' + location.host + '/ws/monitor');

    ws.onopen = () => {
      if (connDot) connDot.classList.add('live');
      if (connLabel) connLabel.textContent = 'live';
    };

    ws.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (e) {
        console.warn('Bad monitor snapshot:', e);
        return;
      }
      if (connTime) connTime.textContent = new Date(data.timestamp * 1000).toLocaleTimeString();
      try {
        onSnapshot(data);
      } catch (e) {
        console.error('Error rendering snapshot:', e);
      }
    };

    ws.onclose = () => {
      if (connDot) connDot.classList.remove('live');
      if (connLabel) connLabel.textContent = 'reconnecting…';
      setTimeout(connect, 2000);
    };

    ws.onerror = () => ws.close();
  }

  connect();
}

// ---- small formatting helpers reused across pages -------------------------
function fmtMs(ms) {
  return ms != null ? Math.round(ms) + ' ms' : '—';
}

function fmtTime(isoString) {
  if (!isoString) return '—';
  try {
    return new Date(isoString).toLocaleTimeString();
  } catch (e) {
    return '—';
  }
}
