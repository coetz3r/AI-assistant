# LUNA Project Log
_Status as of 25 August 2026_

## Overview
- Self-hosted voice AI assistant. Python/aiohttp WebSocket backend.
- Runs on a headless Debian 13 host — currently an old 2014 laptop (AMD A6-6310 APU, 4 CPU cores, integrated Radeon R4, 8GB RAM, 6.9GB swap).
- Robot target hardware: Raspberry Pi 5 or Jetson, depending on budget.
- Explicitly framed as a learning experiment on deliberately underpowered hardware — not a production goal.
- Android phone is the client device for voice interaction.

## Stack
- `llama_cpp` — local LLM inference
- `faster_whisper` — STT
- `Piper` — TTS
- `AsyncGroq` client — optional cloud offload
- `aiohttp` — WebSocket server
- Vanilla JS frontend — RMS-based VAD, barge-in interrupt handling
- HTTPS via `cert.pem` + Tailscale (required for browser mic permission)

## What's built
- `system_stats.py` — CPU/RAM/AMD-GPU-sysfs/network+WiFi collector
- `engine.stats` tracking in `ai_engine.py` — backend used, latency, request counts
- `/monitor` dashboard suite — Overview, AI Activity, Network, Process tabs, one shared `/ws/monitor` feed, dark instrument-panel UI (radial dials, sparklines, no external chart lib)
- `monitor-common.js` — shared websocket/dial/sparkline helpers
- `SystemStats.processes()` and `AIEngine.get_dashboard_stats()` / recent-turns log
- Deploy policy: server always mirrors GitHub exactly; local server edits are discarded, not kept. `auto_pull.sh` does `git fetch` + `git reset --hard origin/main` (was `git pull --ff-only`)
- `tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf` downloaded and tested locally; also tried and reverted from `qwen2.5:1.5b-instruct-q4_k_m`

## Known problems / observations
- Large (~16GB) MoE models caused severe latency from memory thrashing, worsened by full-duplex audio barge-in — this is why the model size came down.
- Local model sometimes free-runs into fake self-dialogue (invented User/Assistant turns, whole stories) instead of stopping after one reply; also loops on repeated phrases.
- Barge-in/STT sometimes triggers on background noise — "hears" speech that wasn't said.
- CPU maxes at ~300% across the 4 cores during local generation, drops after, but the reply still takes time to return. RAM stays under ~1GB throughout — points to a genuine CPU/compute ceiling, not RAM/swap thrashing (that was the earlier 16GB-model problem, now resolved by going smaller).
- Server startup had slowed. Audited with `systemd-analyze blame`: biggest cost was `systemd-journal-flush.service` (12.2s) — journal turned out to be small (11.8MB), pointing to slow disk hardware rather than log bloat — and `dev-sda2.device` (7.8s), the disk itself becoming ready. Neither is fixable by removing software.

## Decisions made
- Deploy server always mirrors GitHub exactly, no local drift.
- Monitoring scope for now: server-side only (not laptop-1/monitor-UI or phone-side).
- No wake-word system — always-on while the app is open, no buttons, continuous conversation.
- AI should ask clarifying questions rather than hallucinate a confident answer on unclear input.
- Wants to remove the cloud LLM fallback from live conversation entirely (breaks voice/personality consistency).
- `bluetooth.service` masked. `ModemManager.service` kept intentionally (used for phone-as-modem tethering). `man-db.timer` flagged as a possible disable but not yet actioned. `cron.service` / `auto_pull.timer` reviewed — auto_pull is functional (keeps deploys synced), not dead weight, so left alone.
- Considering moving memory fact extraction off the cloud LLM and onto a local process/function, to get it out of the response-latency critical path.
- Deprioritizing further boot-time optimization — LLM inference speed is now the main focus.
- Doesn't prioritize the base model's built-in "intelligence" — the design goal is a self-learning system where knowledge accumulates through the memory system, not through model weights.
- Decided to run fully local first despite the current machine being under-equipped, rather than waiting for stronger hardware. Cloud stays available as a swappable fallback function, not the core path.

## Near-term roadmap — next session
1. **Speech vs. noise** — move from RMS-based VAD to a model-based VAD (e.g. Silero VAD), with frame hysteresis and STT confidence (`avg_logprob` / `no_speech_prob`) as a second-stage filter.
2. **Clarification when uncertain** — score confidence from faster_whisper's output; below a threshold, skip the LLM and ask the user to repeat rather than forwarding an uncertain transcript.
3. **Relevant memory retrieval, less cloud reliance** — local RAG. Start with TF-IDF/BM25 over JSON facts (cheap, no extra model loaded), rank by relevance + recency, inject only the top-k.
4. **Token window optimization via JSON** — rolling conversation-summary-buffer pattern; age older raw turns out into a short JSON summary instead of keeping them in the live context window.
5. **Running memory optimizer/cleaner** — periodic (not per-turn) background pass: dedupe near-identical facts, resolve contradictions, prune stale/low-value entries.

## Longer-term goal (after the above is solid)
A numeric emotional/value system — memory and response decisions weighted by importance × urgency × preference (a "salience score"), so the AI has relative preferences, can prioritize what to remember or ask about, and ties memory, actions, and responses together under one scoring system.

## Open model question
- Currently on `tinyllama-1.1b-chat-v1.0.Q4_K_M` — considered outdated for its size class.
- Candidates to A/B test: **Qwen3.5-0.8B** (smaller, likely faster — ~27% fewer params than TinyLlama), **SmolLM2-1.7B**, **Llama-3.2-1B-Instruct**.
- Test on real memory-injected prompts from the actual pipeline (context-fidelity / instruction-following), not general knowledge — the memory system is meant to carry the "knowledge," not the model.
- Also check: llama.cpp's startup log for SIMD support (A6-6310 has AVX but not AVX2 — a build assuming AVX2 silently runs a slow fallback path), confirm `n_threads=4`, and try trimming `n_ctx` for a free speed win.

## Server housekeeping still open
- [x] bluetooth.service masked
- [ ] man-db.timer — flagged, not yet actioned
- [x] journal confirmed small (11.8MB) — not the issue
- [x] cron.service / auto_pull.timer — reviewed, left as-is
