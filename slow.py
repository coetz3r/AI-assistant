"""
SLOW — read-only scoring, plus the curiosity signal.

Slow doesn't retrieve on its own initiative for a turn - fast already
did that. Its job is to look at what fast found and attach
importance/urgency (the "value system"), and to carry the curiosity
signal from the diagram: a running interest weight that will
eventually let the AI ask about things it finds interesting, not just
answer what it's asked. Slow never writes to the DB.
"""


class SlowModule:
    def __init__(self, memory):
        self.memory = memory  # read-only use only - never call upsert_fact here
        self.curiosity_topics = {}  # topic -> weight, grows over time

    def score(self, facts):
        """Attach urgency to each fact fast retrieved (importance
        already lives on the row from when fast wrote it). Sorted so
        orch/LLM sees the most load-bearing facts first."""
        scored = [{**f, "urgency": self._urgency_from_recency(f)} for f in facts]
        scored.sort(key=lambda f: (f["importance"], f["urgency"]), reverse=True)
        return scored

    def _urgency_from_recency(self, fact):
        # Placeholder heuristic - every fact gets the same urgency for
        # now. Swap in something that actually uses last_accessed /
        # created_at once there's real usage data to tune against.
        return 1

    def note_curiosity(self, topic, weight=1):
        self.curiosity_topics[topic] = self.curiosity_topics.get(topic, 0) + weight

    def top_curiosity(self, n=3):
        return sorted(self.curiosity_topics.items(), key=lambda kv: kv[1], reverse=True)[:n]
