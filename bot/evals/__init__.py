"""Golden-set evals for the LLM extractor (real messages from the group).

Not pytest on purpose: these hit the real providers, cost quota, and are
non-deterministic. Run by hand: ``cd bot && python -m evals.run``.
"""
