"""
ORCH — ties fast + slow together, decides what the LLM sees, and owns
the filler timer.

This is the "orch" box in the pipeline diagram: it's the only module
that talks to the LLM, and the only one that starts/stops filler.
Fast and slow never touch the LLM or TTS directly - orch is the
funnel.

Filler content itself (making it sound genuine rather than robotic)
is still an open question - this only builds the timing: a start
delay plus fast's completion signal turns the watchdog off. Wire
on_filler_start/on_filler_stop to real TTS output when that's ready.
"""
import asyncio

from fast import FastModule
from slow import SlowModule
from reason import ReasoningModule


_QUESTION_STARTERS = (
    "who", "what", "when", "where", "why", "how", "which",
    "is", "are", "was", "were", "do", "does", "did",
    "can", "could", "will", "would", "should"
)


def _looks_like_knowledge_question(text):
    t = text.strip().lower()
    if not t:
        return False
    if t.endswith("?"):
        return True
    first_word = t.split()[0] if t.split() else ""
    return first_word in _QUESTION_STARTERS


class Orchestrator:
    def __init__(self, memory, filler_start_delay=1.2, on_filler_start=None, on_filler_stop=None):
        self.reasoning = ReasoningModule()
        self.fast = FastModule(memory, reasoning=self.reasoning)
        self.slow = SlowModule(memory)

        self.filler_start_delay = filler_start_delay
        self.on_filler_start = on_filler_start
        self.on_filler_stop = on_filler_stop
        self._filler_task = None

    async def _filler_watchdog(self):
        try:
            await asyncio.sleep(self.filler_start_delay)
            if self.on_filler_start:
                await self.on_filler_start()
        except asyncio.CancelledError:
            pass

    def _start_filler_watch(self):
        self._filler_task = asyncio.create_task(self._filler_watchdog())

    def _stop_filler_watch(self):
        if self._filler_task and not self._filler_task.done():
            self._filler_task.cancel()
        if self.on_filler_stop:
            asyncio.create_task(self.on_filler_stop())

    def build_context(self, user_text):
        """Runs fast + slow (+ reason via fast) for this turn and
        returns (system_content, mode) - mode is "clarification" or
        "local" so ai_engine knows which kind of turn this is."""
        self._start_filler_watch()

        lookup = self.fast.lookup(user_text)
        facts = self.slow.score(lookup["facts"]) if lookup["matched"] else []

        comparison = None
        if len(facts) >= 2:
            comparison = self.fast.compare_via_reasoning(facts[0], facts[1])

        self._stop_filler_watch()

        needs_clarification = not lookup["matched"] and _looks_like_knowledge_question(user_text)

        if needs_clarification:
            system_content = (
                "You are a direct, concise voice assistant. 1-3 short sentences only. No filler. "
                "You have no internet access and no outside knowledge source, and nothing relevant "
                "turned up in memory for this question. Do NOT answer it and do NOT guess - tell "
                "the user in your own words that you don't have that stored, and ask a short, "
                "natural question to get what you're missing."
            )
            return system_content, "clarification"

        system_content = (
            "You are a direct, concise voice assistant. 1-3 short sentences only. No filler. "
            "You have no internet access and no outside knowledge source. Answer ONLY using "
            "the facts listed below and what the user just said in this turn - never your own "
            "trained/background knowledge. If something isn't covered by these facts, say you "
            "don't have that stored rather than guessing."
        )
        if facts:
            system_content += "\nRelevant facts:\n" + "\n".join(f"- {f['content']}" for f in facts)
            if comparison and comparison.get("related"):
                system_content += f"\nNoted relationship: {comparison['relation']}"
        else:
            system_content += "\nNo relevant facts found in memory for this turn."

        return system_content, "local"
