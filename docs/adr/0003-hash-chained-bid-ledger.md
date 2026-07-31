# ADR-0003 — Hash-chained bid ledger

**Status:** Accepted · **Date:** 2026-07-31

## Context

Auctions involve real money and produce disputes. A losing bidder will
eventually claim the history was altered, or that a bid was inserted after the
fact, or that theirs was removed. "Trust our database" is not an answer, and an
application-level audit log does not help because whoever could alter the bids
could alter the log.

Vehicle auctions in particular attract this scrutiny: the sums are large enough
to argue about and small enough that nobody will fund an audit.

## Options considered

**A. Plain append-only table** with an application convention of never updating.
**B. Database-level immutability** — revoke `UPDATE`/`DELETE`, use triggers.
**C. Hash chain** — each row commits to the digest of the previous row.
**D. External ledger / blockchain anchoring.**

## Decision

**Option C.** Each bid stores `prev_hash` and its own `entry_hash`:

```
entry_hash = SHA256(prev_hash | auction_id | bidder_id | sequence
                    | amount | max_amount | placed_at)
```

`GET /api/v1/auctions/{id}/ledger` recomputes the chain end to end and reports
the exact sequence number where verification fails.

## Why

Option A is a convention, and conventions do not survive an incident or a
migration script. Option B genuinely helps and is complementary — but permissions
can be granted, and it still leaves no *evidence* of what the history used to be.

Option C detects any retroactive edit, insertion, deletion or reordering, because
changing one entry changes its digest and therefore every digest after it. A
single pass finds tampering **and localises it**. It costs one SHA-256 and two
`CHAR(64)` columns per bid — negligible next to a database round trip.

Option D would make the guarantee hold even against the operator, which C does
not. It is genuinely stronger and is the natural next step; the data model
already supports it, since anchoring only requires publishing the head hash
somewhere the operator does not control. Committing to a blockchain for an
internship assignment would be theatre.

## Consequences

**Good**

- Bid history is verifiable by anyone, including the bidders themselves — the
  auction page has a "Verify ledger" button
- Disputes become a query rather than an investigation
- After a database restore, verifying a sample of chains proves nothing was lost
  or corrupted in transit
- Sequence allocation is race-free: it comes from `auctions.last_bid_sequence`,
  a column on the row every bid already locks

**Bad / accepted**

- **This is tamper-evidence, not tamper-proofing.** An operator with full write
  access could recompute the whole chain. Stated plainly in ASSUMPTIONS.md
  rather than overclaimed.
- Bids become genuinely immutable. Any correction must be a compensating entry,
  which is the right accounting discipline but does constrain the admin tooling.
- Verification is O(n) in the number of bids. Fine at auction scale (hundreds);
  a Merkle tree would be needed for millions.
- The digest must be computed deterministically — amounts normalised to 2 dp
  strings, timestamps to microsecond ISO-8601 — or the chain breaks on a driver
  upgrade. This is enforced in one place, `Bid.compute_hash`.
