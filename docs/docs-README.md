# LUNA — Dev Log

Notes from building LUNA, a self-hosted voice AI assistant, mostly on hardware that has no business running a voice AI. This is a hobbyist project, and this folder is the running record of what actually happens when you try.

## What is LUNA?

A self-hosted voice AI assistant built from scratch: a Python/aiohttp WebSocket backend, a local/cloud hybrid LLM pipeline, and a real-time STT/TTS loop (faster-whisper + Piper). The long-term goal is to run it inside a robot, on a Raspberry Pi 5 or Jetson.

## Why document this?

Right now the whole thing runs on a 2014 laptop — an AMD A6-6310 APU, 4 CPU cores, 8GB of RAM, no GPU worth mentioning. That's on purpose. This is an experiment in how far local voice AI can actually go on hardware most people would call obsolete for the job, and there aren't many documented accounts of someone pushing that far. These logs are that account: the real bottlenecks, the dead ends, the things that turned out not to matter, and what actually worked.

If you're a fellow tinkerer with an old machine gathering dust, hopefully this saves you some trial and error — or at least convinces you it's worth trying.

## Log entries

- [2026-08-24 — Project status](./log/2026-08-24-project_log.md)
- [2026-08-25 — Project status](./log/2026-08-25-project_log.md)
- [2026-08-28 — Project status](./log/2026-08-28-project_log.md)

## Stack

- `gemma-3-270m-it-Q4_K_M.gguf` — local LLM inference
- `faster-whisper` — speech-to-text
- `Piper` — text-to-speech
- `aiohttp` — WebSocket backend

New entries get added here as the project moves forward.
