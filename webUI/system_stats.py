"""
system_stats.py — lightweight, dependency-minimal telemetry collector for LUNA.

Reads CPU / memory / (best-effort) AMD GPU / network+WiFi stats without
external services. Every collector is wrapped so a missing sysfs path or
unsupported driver degrades to {"available": False, "note": "..."} instead
of crashing the monitor feed.
"""
import os
import time
import glob
import psutil


class SystemStats:
    def __init__(self):
        self._last_net = psutil.net_io_counters()
        self._last_net_time = time.monotonic()
        # cache which sysfs GPU path worked so we don't re-probe every tick
        self._gpu_busy_path = None
        self._gpu_temp_path = None
        self._gpu_probed = False
        # cache live psutil.Process handles so cpu_percent() reflects a real
        # interval instead of the meaningless 0.0 every process returns on
        # its first-ever call
        self._process_cache = {}

    # ---------------------------------------------------------------- CPU --
    def cpu(self):
        try:
            per_core = psutil.cpu_percent(percpu=True)
            load1, load5, load15 = os.getloadavg()
            freq = psutil.cpu_freq()
            return {
                "available": True,
                "percent_total": sum(per_core) / len(per_core) if per_core else 0,
                "percent_per_core": per_core,
                "freq_mhz": round(freq.current) if freq else None,
                "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
            }
        except Exception as e:
            return {"available": False, "note": str(e)}

    # ------------------------------------------------------------- Memory --
    def memory(self):
        try:
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            return {
                "available": True,
                "used_mb": round(vm.used / 1024 / 1024),
                "total_mb": round(vm.total / 1024 / 1024),
                "percent": vm.percent,
                "swap_used_mb": round(sw.used / 1024 / 1024),
                "swap_total_mb": round(sw.total / 1024 / 1024),
                "swap_percent": sw.percent,
            }
        except Exception as e:
            return {"available": False, "note": str(e)}

    # ---------------------------------------------------------------- GPU --
    def _probe_gpu_paths(self):
        # amdgpu driver (GCN 1.1+, needs modern-ish kernel)
        for busy in glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"):
            self._gpu_busy_path = busy
            break
        # hwmon temp sits alongside, under .../hwmon/hwmonX/temp1_input
        for card_dev in glob.glob("/sys/class/drm/card*/device"):
            for temp in glob.glob(os.path.join(card_dev, "hwmon", "hwmon*", "temp1_input")):
                self._gpu_temp_path = temp
                break
            if self._gpu_temp_path:
                break
        self._gpu_probed = True

    def gpu(self):
        if not self._gpu_probed:
            self._probe_gpu_paths()

        if not self._gpu_busy_path and not self._gpu_temp_path:
            return {
                "available": False,
                "note": "No amdgpu sysfs interface found — older radeon-driver "
                        "APUs (e.g. Kaveri/Beema-era R-series graphics) often "
                        "don't expose gpu_busy_percent. Try `radeontop` as a fallback.",
            }

        result = {"available": True, "busy_percent": None, "temp_c": None}
        try:
            if self._gpu_busy_path:
                with open(self._gpu_busy_path) as f:
                    result["busy_percent"] = int(f.read().strip())
        except Exception:
            pass
        try:
            if self._gpu_temp_path:
                with open(self._gpu_temp_path) as f:
                    result["temp_c"] = round(int(f.read().strip()) / 1000, 1)
        except Exception:
            pass
        return result

    # ------------------------------------------------------------ Network --
    def network(self):
        try:
            now = psutil.net_io_counters()
            t = time.monotonic()
            dt = max(t - self._last_net_time, 1e-6)

            sent_kbps = (now.bytes_sent - self._last_net.bytes_sent) / 1024 / dt
            recv_kbps = (now.bytes_recv - self._last_net.bytes_recv) / 1024 / dt

            self._last_net = now
            self._last_net_time = t

            result = {
                "available": True,
                "sent_kbps": round(sent_kbps, 1),
                "recv_kbps": round(recv_kbps, 1),
                "sent_total_mb": round(now.bytes_sent / 1024 / 1024, 1),
                "recv_total_mb": round(now.bytes_recv / 1024 / 1024, 1),
                "wifi": self._wifi_signal(),
            }
            return result
        except Exception as e:
            return {"available": False, "note": str(e)}

    def _wifi_signal(self):
        # /proc/net/wireless format:
        # Inter-| sta-|   Quality        |   Discarded packets   | Missed | WE
        #  face | tus | link level noise |  nwid  crypt   frag  retry | misc | 22
        #  wlan0: 0000   70.  -40.  -256        0      0      0      0      0        0
        try:
            with open("/proc/net/wireless") as f:
                lines = f.readlines()
            for line in lines[2:]:
                if ":" not in line:
                    continue
                iface, rest = line.split(":", 1)
                fields = rest.split()
                if len(fields) < 3:
                    continue
                return {
                    "available": True,
                    "iface": iface.strip(),
                    "link_quality": float(fields[1]),
                    "signal_dbm": float(fields[2]),
                }
        except Exception:
            pass
        return {"available": False}

    # ------------------------------------------------------------ Process --
    def processes(self, limit=8):
        try:
            current_pids = set(psutil.pids())

            # drop cached handles for processes that have since exited
            for pid in list(self._process_cache.keys()):
                if pid not in current_pids:
                    del self._process_cache[pid]

            rows = []
            for pid in current_pids:
                proc = self._process_cache.get(pid)
                if proc is None:
                    try:
                        proc = psutil.Process(pid)
                        # Prime the internal timer. This first call always
                        # returns a meaningless 0.0 / non-blocking value —
                        # the real percentage only shows up on the *next*
                        # tick, once there's an actual interval to measure.
                        proc.cpu_percent(None)
                        self._process_cache[pid] = proc
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                try:
                    with proc.oneshot():
                        name = proc.name()
                        cpu = proc.cpu_percent(None)
                        mem = proc.memory_percent()
                    rows.append({
                        "pid": pid,
                        "name": name,
                        "cpu_percent": round(cpu, 1),
                        "memory_percent": round(mem, 1),
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            return {
                "available": True,
                "total_count": len(rows),
                "top_cpu": sorted(rows, key=lambda r: r["cpu_percent"], reverse=True)[:limit],
                "top_memory": sorted(rows, key=lambda r: r["memory_percent"], reverse=True)[:limit],
            }
        except Exception as e:
            return {"available": False, "note": str(e)}

    # ------------------------------------------------------------------- --
    def collect_all(self, engine_stats=None, active_connections=None):
        return {
            "timestamp": time.time(),
            "cpu": self.cpu(),
            "memory": self.memory(),
            "gpu": self.gpu(),
            "network": self.network(),
            "processes": self.processes(),
            "engine": engine_stats or {},
            "active_connections": active_connections if active_connections is not None else 0,
        }
