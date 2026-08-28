// LUNA monitor — Network page.
// Relies on monitor-common.js (loaded first) for the websocket connection
// and shared formatting/sparkline helpers.

const netUpHistory = [];
const netDownHistory = [];

function applySnapshot(data) {
  const net = data.network;
  if (!net || !net.available) return;

  document.getElementById('netUp').textContent = net.sent_kbps.toFixed(1) + ' KB/s';
  document.getElementById('netDown').textContent = net.recv_kbps.toFixed(1) + ' KB/s';

  pushHistory(netUpHistory, net.sent_kbps);
  pushHistory(netDownHistory, net.recv_kbps);
  drawSparkline(document.getElementById('netSpark'), [
    { data: netUpHistory, color: '#4FD8E8' },
    { data: netDownHistory, color: '#E8A23D' },
  ], { minMax: 20, noFill: true });

  const wifi = net.wifi;
  if (wifi && wifi.available) {
    document.getElementById('wifiSignal').textContent = wifi.iface + ' · ' + wifi.signal_dbm + ' dBm';
    document.getElementById('wifiIface').textContent = wifi.iface;
    document.getElementById('wifiQuality').textContent = wifi.link_quality;
    document.getElementById('wifiDbm').textContent = wifi.signal_dbm + ' dBm';
    document.getElementById('wifiNote').textContent = '';
  } else {
    document.getElementById('wifiSignal').textContent = 'no wireless interface';
    document.getElementById('wifiIface').textContent = '—';
    document.getElementById('wifiQuality').textContent = '—';
    document.getElementById('wifiDbm').textContent = '—';
    document.getElementById('wifiNote').textContent = 'No wireless interface detected — this host may be on wired ethernet.';
  }

  document.getElementById('totalSent').textContent = net.sent_total_mb + ' MB';
  document.getElementById('totalRecv').textContent = net.recv_total_mb + ' MB';
}

connectMonitorSocket(applySnapshot);
