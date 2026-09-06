from __future__ import annotations

from typing import Iterable


def opposing_claim_ids(
    *,
    new_claim: dict,
    existing_claims: Iterable[dict],
) -> list[str]:
    """Exact-rule contradiction.

    If subject matches and polarity is opposite, older claims are refuted.
    """
    subject = new_claim.get("subject")
    polarity = new_claim.get("polarity")
    opposites = []
    for prior in existing_claims:
        if prior.get("status") == "refuted":
            continue
        if prior.get("subject") != subject:
            continue
        if prior.get("polarity") == polarity:
            continue
        if prior.get("id"):
            opposites.append(prior["id"])
    # Keep unique, oldest first.
    seen = set()
    unique: list[str] = []
    for cid in opposites:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)
    return unique
