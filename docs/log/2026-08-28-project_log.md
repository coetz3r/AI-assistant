# LUNA Dev Log — Fast/Slow/Reason/Orch Pipeline Build

**Date:** August 28, 2026
**Branch:** `main`
**Files touched:** `ai_engine.py`, `memory_manager.py`
**Files created:** `fast.py`, `slow.py`, `reason.py`, `orchestrator.py`

---

## 1. Session goals and predicted outcomes

This session focuses on designing the central intelligence and recall architecture by framing the fast and slow retrieval branches alongside the orchestrator as the primary memory system. Within this scope, the primary objective is to evaluate ultra-lightweight models—specifically testing Gemma 3 270M in specialized roles (such as intent classification, fast memory retrieval, or output verification)—to determine whether offloading low-overhead tasks to a 270M parameter footprint significantly reduces end-to-end latency while retaining sufficient accuracy for reliable pipeline orchestration.

Two primary goals: get something running, and lock Gemma down to internal memory only, no internet.

---

## 2. Blocking Gemma from answering off its own trained knowledge

**The ask:** Gemma should only ever answer from what's in memory — never fall back on whatever it picked up in pretraining, and never reach out to the internet.

**First pass:** Added a `_looks_like_knowledge_question()` heuristic (text ends in `?`, or opens with a who/what/when/where/why/how-type word). If a turn matched that *and* memory recall came back empty, the code skipped calling Gemma entirely and returned a hardcoded string: *"I don't have anything stored about that yet — can you tell me?"*

**Correction (you caught this):** That hardcoded string wasn't what you wanted — you'd specifically wanted the LLM generating its own clarification, not a canned line. Reworked it so Gemma is *always* called, but the system prompt branches:

- **No memory match on a question-like turn** → prompt explicitly forbids answering or guessing, and instructs Gemma to say (in its own words) that it doesn't have that stored and ask a short natural follow-up.
- **Everything else** → prompt restricts Gemma to only the facts listed under "Relevant facts" plus what the user just said this turn — never its own trained/background knowledge.

Turn logging distinguishes the two cases (`backend = "clarification"` vs `"local"`) so the monitor dashboard can track how often LUNA is coming up empty on recall.

**Note on the internet-access part specifically:** nothing in this codebase makes outbound network calls — `llama_cpp`, `faster_whisper`, and Piper all run local, `MemoryManager` is pure SQLite. So "no internet" was already true structurally; the actual gap being closed here was Gemma's *pretrained* knowledge leaking into answers, not a literal network connection.

---

## 3. Building the fast/slow/reason/orch pipeline

This was the main piece of work this session — turning the diagram into real, running code instead of the single-LLM-plus-flat-memory setup.

### `fast.py`
- Pure keyword lookup against the DB — no scoring, no interpretation, just match/no-match. Reuses `MemoryManager.get_relevant_memories()` under the hood.
- The **only** module in the system allowed to write to the DB. Every write goes through a small rule-based verifier first (`_verify_fact`): rejects empty/too-short content and exact duplicates. Intentionally simple for now — same interface will hold if a smarter (e.g. LLM-based) verifier replaces it later.
- Exposes `compare_via_reasoning(fact_a, fact_b)` — fast decides when two retrieved facts are worth comparing and hands them straight to `reason`, matching the bidirectional fast↔reason link in your diagram.

### `slow.py`
- Read-only. Never calls `upsert_fact` — takes what `fast` retrieved and attaches urgency on top of the importance value already stored on each fact (the "value system"). Sorts by `(importance, urgency)` descending.
- Urgency itself is currently a placeholder constant (`return 1` for every fact) — flagged as a stub, not a finished scoring model.
- Carries the **curiosity** signal from the diagram: `note_curiosity(topic, weight)` / `top_curiosity(n)` — a running interest-weight dict. Not wired into anything yet, but the hook exists for the "AI can ask about things it's curious about" goal.

### `reason.py`
- The comparator. Takes two facts (dicts or strings) from `fast`, lowercases and tokenizes both, and reports shared terms as a "relation." Never queries the DB itself — it only ever sees what `fast` hands it.
- Still open, per your earlier note: rule-based for now, could lean on the LLM for fuzzier comparisons later. Same `.compare()` interface either way, so it's a drop-in swap.

### `orchestrator.py`
- The only module that talks to the LLM. `build_context(user_text)` runs `fast.lookup()`, then `slow.score()` on whatever came back, then (if 2+ facts scored) `fast.compare_via_reasoning()` on the top two, and returns `(system_content, mode)` — mode is `"clarification"` or `"local"`.
- Owns the filler timer end-to-end: `_start_filler_watch()` kicks off an async watchdog on entry to `build_context()`, `_stop_filler_watch()` cancels it once fast/slow/reason finish. `on_filler_start` / `on_filler_stop` are callback hooks — **not wired to real TTS yet**, this only builds the timing skeleton (start-delay + fast's completion signal, per what you resolved earlier). Making the filler content itself sound genuine rather than robotic is still an open question, not addressed this session.
- The clarification-prompt and answer-prompt text moved here from `ai_engine.py` — this is now the single place that owns what the LLM sees.

### Wiring into `ai_engine.py`
- `__init__` now constructs `self.orchestrator = Orchestrator(self.memory)` alongside the existing `MemoryManager`.
- `generate_response()`'s old memory-recall-then-build-a-prompt block was replaced with one call: `system_content, mode = self.orchestrator.build_context(user_text)`.
- `_bg_memory_task()` (the background fact-extraction step) now writes through `self.orchestrator.fast.write(...)` instead of calling `self.memory.upsert_fact()` directly — so the write-path verifier is actually in the loop, not bypassed.

---

## 4. Bug found and fixed: `memory_manager.py` keyword matching

While smoke-testing the new pipeline, a real bug turned up: `get_relevant_memories()` split the query with plain `.split()`, so a trailing `?` stayed glued to the last word (`"bicycle?"`). That string never matched a stored `"bicycle"` fact — `fast`'s recall was silently missing hits, which in turn meant `reason` sometimes never got a second fact to compare.

**Fix:** swapped the naive split for `re.findall(r"[a-zA-Z']+", query_text.lower())`, which strips punctuation before the length filter. Re-ran the smoke test afterward and confirmed both facts now surface correctly.

---

## 5. Verification

Ran two live smoke tests against a throwaway SQLite DB (`/tmp/luna_smoke*.db`) — not just a syntax check, the actual pipeline logic executing:

1. **Empty memory + a question** → `orchestrator.build_context()` correctly returned `mode == "clarification"`.
2. **Two facts written via `fast.write()`** — a duplicate ("favorite color is teal" submitted twice) was correctly rejected by the verifier; the two distinct facts were inserted.
3. **After the punctuation fix**, a query touching both facts ("what color is my bicycle?") correctly retrieved both, `slow` scored and sorted them, and `fast.compare_via_reasoning()` correctly flagged the shared term ("teal") between them — that relationship got folded into the LLM's system prompt automatically.
4. Confirmed the filler watchdog didn't fire when `fast`/`slow`/`reason` finished well inside the start-delay window.
5. **Gemma's performance:** Gemma preformed fairly well during inference, latency is a concern but will work more on that. On average inference takes less than a second.

All files pass `py_compile`. **Needs complete detail testing:** active Gemma inference, STT/TTS, or the WebSocket server — this sandbox has no GGUF weights, no mic/audio path, and no network access to the ML library ecosystems needed to install `llama_cpp`/`faster_whisper`/`piper`. Everything here is verified at the pipeline-logic level; it still needs a run on your actual server to confirm real-world timing and Gemma's behavior under the new prompts. 

---

## 6. Known stubs / open items for next session

- **Filler content** — timing hooks exist (`on_filler_start`/`on_filler_stop`), nothing generates or plays actual filler speech yet.
- **Output-path verifier** (LLM → TTS, catching drift/hallucination before speech) — confirmed as staying in the design, not built this session.
- **Slow's urgency scoring** — placeholder constant, not yet using `last_accessed`/`created_at`.
- **Fast → orch "user is getting impatient" signal** — As a lightweight event fast should be able to send orch mid-wait; not implemented.
- **Curiosity signal** — `note_curiosity()`/`top_curiosity()` exist on `slow` but nothing calls them yet.
