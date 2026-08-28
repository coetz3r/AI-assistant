"""
FAST — pure lookup + the only write path to the DB.

Per the agreed pipeline:
  - Read: quick keyword-based recall from memory. No scoring, no
    interpretation - just "does this match, yes/no". Scoring is
    slow's job, not fast's.
  - Write: fast is the ONLY module allowed to write new facts to the
    DB (slow and reason are read-only). Every write passes through a
    small verifier first so obviously-bad facts don't get stored.
  - Comparator hookup: when it has two retrieved facts worth
    comparing, it calls reason directly (the bidirectional fast<->
    reason link in the diagram) rather than going through orch.
"""
import time
from memory_manager import MemoryManager


def _verify_fact(content, existing_facts):
    """Write-path verifier. Deliberately simple/rule-based for now -
    rejects empty/too-short content and exact duplicates. This can be
    swapped for something smarter later without touching fast's
    public interface."""
    if not content or len(content.strip()) < 3:
        return False, "empty or too short"
    lc = content.strip().lower()
    for f in existing_facts:
        if lc == f["content"].strip().lower():
            return False, "exact duplicate"
    return True, "ok"


class FastModule:
    def __init__(self, memory: MemoryManager, reasoning=None):
        self.memory = memory
        self.reasoning = reasoning

    def lookup(self, user_text):
        """Pure retrieval: word-match against the DB. No scoring."""
        start = time.time()
        facts, _ = self.memory.get_relevant_memories(user_text)
        duration = time.time() - start
        return {
            "matched": len(facts) > 0,
            "facts": facts,
            "duration": duration,
        }

    def compare_via_reasoning(self, fact_a, fact_b):
        """Fast decides two retrieved facts are worth comparing and
        hands them straight to reason - reason never queries the DB
        on its own."""
        if self.reasoning is None:
            return None
        return self.reasoning.compare(fact_a, fact_b)

    def write(self, content, m_type, importance=5, confidence=1.0):
        """The only path in the system that writes to the DB."""
        existing, _ = self.memory.get_relevant_memories(content, limit=25)
        ok, reason = _verify_fact(content, existing)
        if not ok:
            return {"written": False, "reason": reason}
        status, _ = self.memory.upsert_fact(content, m_type, importance, confidence)
        return {"written": True, "status": status}
