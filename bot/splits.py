"""Pure math for non-even splits (BY_AMOUNT / BY_PERCENTAGE).

The LLM only transcribes the numbers that appear literally in the message
(anything else invites arithmetic hallucinations — we've seen 16700 become
17700 in production). Everything derived — the remainder, the currency
conversion, the rounding — happens here, where it can be unit-tested.

Units are integers throughout: cents of the original currency for BY_AMOUNT,
basis points (of 10000) for BY_PERCENTAGE.
"""

from __future__ import annotations


class SplitError(Exception):
    """A split that can't be honored; ``reason`` picks the clarification."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def fill_remainder(total: int, parts: list[int | None]) -> list[int]:
    """Resolve ``None`` entries ("el resto") into concrete integer parts.

    The remainder is split evenly among the ``None`` entries, distributing the
    non-divisible leftover one unit at a time from the first hole on. Raises
    SplitError when the explicit parts exceed the total, don't reach it with
    no hole to absorb the difference, leave a zero remainder, or when any
    resulting part is <= 0.
    """
    if not parts:
        raise SplitError("empty")
    explicit = sum(p for p in parts if p is not None)
    holes = [i for i, p in enumerate(parts) if p is None]
    if explicit > total:
        raise SplitError("sum_exceeds_total")
    if not holes and explicit != total:
        raise SplitError("sum_mismatch")
    if holes and explicit == total:
        raise SplitError("zero_remainder")
    out: list[int] = list(parts)  # type: ignore[arg-type]
    if holes:
        base, extra = divmod(total - explicit, len(holes))
        for j, i in enumerate(holes):
            out[i] = base + (1 if j < extra else 0)
    if any(p <= 0 for p in out):
        raise SplitError("zero_share")
    return out


def convert_shares(orig_cents: list[int], total_group_cents: int, rate: float) -> list[int]:
    """Convert original-currency parts to group-currency cents summing exactly.

    ``rate`` must be the very same ``conversion_rate`` used for the expense
    total, so each share lands where the user expects. Per-share rounding can
    leave the sum a few cents off ``total_group_cents``; the largest share
    absorbs the difference (deterministic, smallest relative distortion).
    """
    shares = [round(c * rate) for c in orig_cents]
    diff = total_group_cents - sum(shares)
    if diff:
        shares[shares.index(max(shares))] += diff
    if any(s <= 0 for s in shares):
        raise SplitError("rounding_wiped_share")
    return shares
