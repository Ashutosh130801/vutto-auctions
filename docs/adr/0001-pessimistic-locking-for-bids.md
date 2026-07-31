# ADR-0001 — Pessimistic row locking for bid placement

**Status:** Accepted · **Date:** 2026-07-31

## Context

Multiple bidders place bids on the same auction simultaneously, served by
different stateless API replicas. Bid placement must decide the new price, the
new leader, whether to extend the auction, and how to move deposit holds — all
based on state that another request may be changing at the same instant.

A read-modify-write loses updates: two requests read `current_price = 50,000`,
both conclude they lead, and the second write silently overwrites the first. The
auction ends with a price no ledger entry explains and possibly the wrong winner.
This is the single highest-risk correctness problem in the system.

## Options considered

**A. Optimistic concurrency** — read with a `version`, compute, write with
`WHERE version = :seen`, retry on zero rows affected.

**B. Pessimistic row locking** — `SELECT ... FOR UPDATE` on the auction row at
the start of the transaction.

**C. `SERIALIZABLE` isolation** — let PostgreSQL detect conflicts and abort.

**D. Application-level distributed lock** (Redis Redlock).

## Decision

**Option B.** Every bid runs in one transaction that opens with
`SELECT ... FROM auctions WHERE id = :id FOR UPDATE`.

## Why

The traffic pattern that matters is a burst of bidders converging on the last
seconds of *one* popular auction — precisely the pattern where optimistic
concurrency degenerates. Everyone reads the same version, everyone collides,
everyone retries, everyone collides again. Throughput collapses exactly when the
system is most visible. Pessimistic locking queues bidders in arrival order and
each waiter re-reads fresh state, so every attempt makes progress.

`SERIALIZABLE` (C) would be correct but pushes retry handling into every caller
and produces the same livelock under contention, with less predictable failure
modes.

A Redis lock (D) would put correctness in a component we deliberately treat as an
accelerator. If Redis is unreachable, the choice is between refusing all bids and
accepting incorrect ones. Neither is acceptable, so correctness stays entirely
inside the database transaction.

Critically, the lock is **per auction**. 500 live auctions are 500 independent
lock domains bidding in parallel. Throughput scales with the number of live
auctions — the dimension that actually grows — rather than being capped by one
global mutex.

## Consequences

**Good**

- Lost updates are impossible, not merely unlikely
- Every accepted bid is atomic across the auction row, the ledger, deposit holds
  and outbound events
- Different auctions never contend
- `version` is freed up to be a *staleness signal for clients* rather than a
  write guard

**Bad / accepted**

- Single-auction throughput is capped at roughly 50–100 bids/sec. Well beyond
  what this platform needs; sharding is the answer if that ever changes.
- Lock ordering must be disciplined. A bid may lock two deposit accounts, so we
  impose a global order — auction row first, then deposit accounts by ascending
  `user_id` — which removes the deadlock cycle rather than relying on the
  database's detector.
- A long-running transaction blocks bidders on that auction, so nothing slow
  (network calls, external APIs) may happen inside the locked section.

**Verified by:** `tests/test_concurrency.py` — 200 simultaneous bids, 50
identical maximums, deposit-hold contention, anti-snipe bursts, and ten parallel
auctions, each asserting the five system invariants.
