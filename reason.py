"""
REASON — the comparator.

Finds relationships between two facts fast hands it. Runs close to
fast's timing and only fires when fast decides a comparison is worth
making - it never queries the DB on its own.

Still open (per design notes): whether this stays pure rule-based or
leans on the LLM for fuzzier comparisons. This is a rule-based
starting point - same interface either way, so it's a drop-in swap
later.
"""


class ReasoningModule:
    def compare(self, fact_a, fact_b):
        a = fact_a.get("content", "") if isinstance(fact_a, dict) else str(fact_a)
        b = fact_b.get("content", "") if isinstance(fact_b, dict) else str(fact_b)

        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        overlap = a_words & b_words

        if not overlap:
            return {"related": False, "shared_terms": []}

        return {
            "related": True,
            "shared_terms": sorted(overlap),
            "relation": f"Both mention: {', '.join(sorted(overlap))}",
        }
