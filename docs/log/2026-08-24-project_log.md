# LUNA Dev Log — 2026-08-24

## Overview
LUNA is a self-hosted voice AI assistant: Python/aiohttp WebSocket backend, local/cloud hybrid LLM inference, real-time STT/TTS pipeline. Runs on a headless Debian 13 host (AMD A6-6310 APU, 4 CPU cores, integrated Radeon R4, 8GB RAM, 6.9GB swap). Android phone is the client for voice interaction.

## Built this session
- `system_stats.py` — CPU/RAM/AMD-GPU-sysfs/network+WiFi collector
- `engine.stats` tracking in `ai_engine.py` — backend used, latency, request counts
- Initial `/monitor` dashboard — dark instrument-panel UI, radial dials and sparklines, no external chart library, matching the existing vanilla-JS style
- Expanded `/monitor` from one page into four: **Overview** (detailed CPU/RAM/GPU with sparklines, per-core readouts, top-process footnotes), **AI Activity** (turn counts, local/cloud split, latency history, recent-turns table, memory/fact growth), **Network** (throughput sparklines, WiFi detail, totals since boot), **Process** (top-CPU and top-memory process tables, process-count history) — all sharing one `/ws/monitor` feed
- `monitor-common.js` — shared websocket/dial/sparkline helpers
- `SystemStats.processes()` (cached psutil.Process handles for accurate CPU deltas) and `AIEngine.get_dashboard_stats()` / recent-turns log to support the new dashboard pages

## Decisions
- Deploy server always mirrors GitHub exactly — local edits made directly on the server (e.g. to `install.sh`) are discarded rather than kept. `auto_pull.sh` changed from `git pull --ff-only` to `git fetch` + `git reset --hard origin/main` accordingly.
- Monitoring scope for now: server-side only (not laptop-1/monitor-UI or phone-side resource reporting).
- No wake-word system — AI stays active while the app is open, no buttons, natural continuous conversation.
- AI should ask clarifying questions when uncertain what was heard, rather than hallucinating a confident response.
- Wants to remove the cloud LLM fallback from live conversation entirely — it breaks voice/personality consistency ("like talking to someone else").
- Wants to reduce/control cloud token use for memory fact-extraction rather than extracting from every turn.
- Tried `qwen2.5:1.5b-instruct-q4_k_m.gguf`, reverted back to the smaller `tinyllama-1.1b-chat-v1.0.Q4_K_M`, with a plan to try `qwen2.5:1.5b-instruct` again later and rework the baseline.

## Observations / problems noted
- Severe latency from memory thrashing when running large (~16GB) MoE models locally, worsened by full-duplex audio barge-in overhead.
- Local model sometimes free-runs into fake self-dialogue (invented User/Assistant turns, whole invented stories) instead of stopping after one reply; also loops on repeated phrases.
- Barge-in/STT system sometimes triggers on background noise — "hears" speech that wasn't said.
- The 2048-token context window fills up quickly.

## Next workstreams identified
- Memory creation/retrieval
- An intelligent background-noise/interrupt filter
- Eventually: run the AI inside a robot
