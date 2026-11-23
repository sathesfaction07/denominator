"""Making one answer out of several sources' claims.

Two separate decisions live here, and both are policy rather than measurement:

**Identity.** "How many distinct records did we get?" has no answer until somebody says what makes
two records from different sources the same record. Union on each source's own id and every row is
distinct; union on a shared key and the count drops. Same rows, different totals.

**Conflict.** Once two sources are agreed to be describing one thing and they disagree about a
value, the three strategies the uplift asks for give different answers on the same claims -- and
the output carries the answer without carrying which strategy produced it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .models import POLICY_NOTES, ConflictPolicy, Identity, Record


@dataclass(frozen=True)
class Union:
    """The result of collapsing records under one identity scheme."""

    identity: Identity
    rows: int
    distinct: int
    unkeyed: tuple[str, ...]

    @property
    def collapsed(self) -> int:
        return self.rows - self.distinct

    def summary(self) -> str:
        if self.identity is Identity.NONE:
            return (
                f"With no identity scheme, {self.rows} rows stay {self.rows} rows. Nothing is "
                "wrong with that answer; it just is not a count of THINGS."
            )
        note = (
            f"{self.rows} rows collapse to {self.distinct} under {self.identity.value}, "
            f"merging {self.collapsed}."
        )
        if self.unkeyed:
            note += (
                f" {len(self.unkeyed)} row(s) carry no key under this scheme and were kept "
                "separate -- which is NOT the same as being unique, only unmatched."
            )
        return note


def union(records: list[Record], identity: Identity) -> Union:
    """Collapse records under one identity scheme, keeping the unkeyed ones visible."""
    keys: list[str] = []
    unkeyed: list[str] = []
    for r in records:
        key = r.key(identity)
        if key is None:
            unkeyed.append(f"{r.source}:{r.native_id}")
        else:
            keys.append(key)
    distinct = len(set(keys)) + len(unkeyed)
    return Union(
        identity=identity, rows=len(records), distinct=distinct, unkeyed=tuple(unkeyed)
    )


@dataclass(frozen=True)
class IdentityComparison:
    """One set of records counted under every scheme."""

    unions: tuple[Union, ...]

    @property
    def totals(self) -> dict[Identity, int]:
        return {u.identity: u.distinct for u in self.unions}

    @property
    def schemes_agree(self) -> bool:
        return len({u.distinct for u in self.unions}) == 1

    def summary(self) -> str:
        counts = ", ".join(f"{u.identity.value}={u.distinct}" for u in self.unions)
        if self.schemes_agree:
            return (
                f"Every scheme gives the same count ({counts}). That is a property of this data "
                "having no cross-source overlap, not a property of the schemes."
            )
        return (
            f"The same rows count differently under each scheme: {counts}. 'How many records do "
            "we have' is answered by a decision about identity, not by the data."
        )


def compare_identities(records: list[Record]) -> IdentityComparison:
    return IdentityComparison(unions=tuple(union(records, i) for i in Identity))


@dataclass(frozen=True)
class Resolution:
    """One policy's answer, and what it needed to produce it."""

    policy: ConflictPolicy
    value: str | None
    usable: bool
    detail: str


def resolve(claims: list[Record], policy: ConflictPolicy) -> Resolution:
    """Apply one policy, and refuse when its precondition is absent.

    Each policy needs something the claims may not carry. Returning a value anyway -- by falling
    back to "first one wins" -- is how a strategy pattern quietly stops being the strategy that
    was chosen.
    """
    if not claims:
        return Resolution(policy, None, False, "No claims, so nothing to resolve.")

    values = {c.value for c in claims}
    if len(values) == 1:
        return Resolution(
            policy, claims[0].value, True,
            "Every source agrees, so the policy did not matter here. Most of the time it will "
            "not, which is how a badly chosen policy survives.",
        )

    if policy is ConflictPolicy.FLAG_FOR_REVIEW:
        return Resolution(
            policy, None, True,
            f"{len(values)} different values across {len(claims)} claims, and this policy "
            "declines to pick. It is the only one that does not invent a winner.",
        )

    if policy is ConflictPolicy.LATEST_WINS:
        timed = [c for c in claims if c.seen_at]
        if len(timed) < len(claims):
            return Resolution(
                policy, None, False,
                f"{len(claims) - len(timed)} claim(s) carry no timestamp, so there is no 'latest'. "
                "Picking among the ones that do would silently exclude the others.",
            )
        winner = max(timed, key=lambda c: c.seen_at or "")
        return Resolution(
            policy, winner.value, True,
            f"{winner.source} was seen last ({winner.seen_at}), so its value wins. This prefers "
            "recency over correctness, and a source that refreshes often always wins.",
        )

    if policy is ConflictPolicy.HIGHEST_CONFIDENCE:
        scored = [c for c in claims if c.confidence is not None]
        if len(scored) < len(claims):
            return Resolution(
                policy, None, False,
                f"{len(claims) - len(scored)} claim(s) carry no confidence, so they cannot be "
                "compared. Treating a missing confidence as zero would invent a ranking.",
            )
        winner = max(scored, key=lambda c: c.confidence or 0.0)
        return Resolution(
            policy, winner.value, True,
            f"{winner.source} is most confident ({winner.confidence}), so its value wins. Those "
            "numbers were supplied by the sources being compared.",
        )

    counts = Counter(c.value for c in claims)
    top, n = counts.most_common(1)[0]
    tied = [v for v, k in counts.items() if k == n]
    if len(tied) > 1:
        return Resolution(
            policy, None, False,
            f"{len(tied)} values tie at {n} vote(s) each, so there is no majority. Breaking the "
            "tie by order would hand the answer to whichever source replied first.",
        )
    # No `len(claims) < 3` branch: with two differing claims the vote is always tied, so the tie
    # check above has already returned. POLICY_NOTES carries the warning about small votes.
    return Resolution(
        policy, top, True,
        f"'{top}' has {n} of {len(claims)} votes. This counts sources rather than weighing them, "
        "so two systems fed by the same upstream outvote one independent observation.",
    )


@dataclass(frozen=True)
class PolicyComparison:
    """Every policy, on one set of claims."""

    resolutions: tuple[Resolution, ...]

    @property
    def distinct_answers(self) -> int:
        return len({r.value for r in self.resolutions if r.usable and r.value is not None})

    @property
    def inoperable(self) -> tuple[ConflictPolicy, ...]:
        """Policies that could not run at all, because the claims lack what they need.

        Kept separate from FLAG_FOR_REVIEW, which returns no value ON PURPOSE. "Cannot run" and
        "declines to pick" are not the same answer.
        """
        return tuple(r.policy for r in self.resolutions if not r.usable)

    @property
    def policies_agree(self) -> bool:
        """Whether the choice of policy is irrelevant on these claims.

        It is only irrelevant if every policy could run AND they all reached the same value.
        Comparing values alone was the first version of this and it was wrong: on claims where
        only one policy can run, the values trivially "agree" while the choice of policy decides
        everything.
        """
        return self.distinct_answers <= 1 and not self.inoperable

    @property
    def policy_decides_the_outcome(self) -> bool:
        return not self.policies_agree

    def summary(self) -> str:
        usable = [r for r in self.resolutions if r.usable and r.value is not None]
        blocked = self.inoperable
        if not usable:
            return (
                "No policy could produce a value on these claims. That is an answer: the claims "
                "do not carry what any of these strategies need."
            )
        if self.policies_agree:
            return (
                f"Every policy could run and all of them return '{usable[0].value}'. The choice of "
                "policy did not matter FOR THIS FIELD, which is not the same as not mattering."
            )
        shown = ", ".join(f"{r.policy.value}='{r.value}'" for r in usable)
        if self.distinct_answers > 1:
            note = (
                f"{self.distinct_answers} different answers from one set of claims: {shown}. "
            )
        else:
            note = f"Only {len(usable)} policy could produce an answer at all: {shown}. "
        if blocked:
            note += (
                f"{len(blocked)} policy/policies could not run here "
                f"({', '.join(p.value for p in blocked)}), so the claims themselves ruled out "
                "options somebody thought they had chosen between. "
            )
        return note + (
            "The strategy is a POLICY, nothing in the data prefers one, and a report that prints "
            "only the winner has hidden the decision that produced it."
        )


def compare_policies(claims: list[Record]) -> PolicyComparison:
    return PolicyComparison(resolutions=tuple(resolve(claims, p) for p in ConflictPolicy))


def policy_requirements() -> list[tuple[ConflictPolicy, str]]:
    return [(p, POLICY_NOTES[p]) for p in ConflictPolicy]
