# ADR-0002 — Transactional outbox for realtime events

**Status:** Accepted · **Date:** 2026-07-31

## Context

When a bid is accepted, everyone watching that auction must see the new price
within about a second, across however many API replicas hold their sockets.

The obvious implementation — commit the bid, then publish to Redis — contains a
dual-write problem. Between the commit and the publish, the process can crash or
Redis can be unreachable. The bid is durable but nobody was told, and there is no
record that anything was missed. Spectators see a stale price indefinitely, and
the operator has no signal that it happened.

## Options considered

**A. Publish inline** after the commit.
**B. Publish inside the transaction**, before commit.
**C. Transactional outbox** — write events to a table in the same transaction; a
relay publishes them asynchronously.
**D. Change Data Capture** (logical replication → Debezium → Kafka).

## Decision

**Option C.** Events are inserted into `outbox_events` in the same transaction as
the state change. A relay worker claims undispatched rows with
`FOR UPDATE SKIP LOCKED` and publishes them to Redis pub/sub.

## Why

Option A has the failure window described above. Option B is worse: it publishes
events for a transaction that may still roll back, so subscribers can see bids
that never happened — a corruption you cannot walk back.

Option D is the industrially "correct" answer at large scale, and where I would
go if event volume justified it. It also means running Kafka and Debezium, which
is a large operational commitment for a platform of this size. The outbox gets
the same delivery guarantee with one table and one loop.

The outbox also makes failure *observable*. `outbox_pending_events` is a single
gauge that answers "is realtime degraded right now?" — and the answer is never
"we lost some events", only "some events have not been delivered yet".

## Consequences

**Good**

- "State changed but nobody was told" cannot happen
- Redis down → events queue durably and drain automatically on recovery
- N relay replicas partition work via `SKIP LOCKED`, with no coordination
- One gauge tells the on-call whether realtime is healthy
- The same outbox is the natural feed for email, SMS or analytics later, with no
  changes to the bidding path

**Bad / accepted**

- One extra table and one extra write per state change
- Up to ~250 ms of added latency from the polling interval. `LISTEN/NOTIFY` would
  cut this to near zero; polling is simpler and more robust to restarts, and
  250 ms is imperceptible next to a two-minute anti-snipe window.
- Delivery is **at-least-once**, so clients must be idempotent. They already are:
  frames are guarded by the monotonic `auction.version`.
- The table needs periodic pruning of dispatched rows.
