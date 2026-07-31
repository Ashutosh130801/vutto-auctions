# Architecture

## 1. The problem, honestly stated

An auction platform looks like CRUD until you look at the last thirty seconds of
a popular auction. Then it is a distributed systems problem wearing a web app's
clothes:

- **Concurrent writes to one row.** Dozens of bidders converge on a single
  auction. A read-modify-write loses bids and can produce a price no one bid.
- **Fan-out.** Everyone watching must see the new price within a second, across
  however many API replicas are serving them.
- **A hard deadline.** "When exactly did this close?" must have one answer, and
  it must not depend on which server's clock you ask, or on a cron job firing.
- **Money.** Deposits are held and released as the lead changes. Double-holding
  or failing to release is a customer-facing financial bug.
- **Disputes.** A losing bidder will eventually claim the history was altered.
  You want to be able to *prove* it was not.

Everything below follows from taking those five things seriously.

---

## 2. System shape

```
                        ┌──────────────────────────────┐
   Browser ────HTTP────▶│  nginx  (SPA + reverse proxy)│
      │                 └──────────────┬───────────────┘
      │                                │  /api  (HTTP + WS upgrade)
      │                                ▼
      │                 ┌──────────────────────────────┐
      └────WebSocket───▶│   FastAPI API   × N replicas │
                        │  ┌────────────────────────┐  │
                        │  │ bidding engine         │  │
                        │  │ auction lifecycle      │  │
                        │  │ auth / RBAC            │  │
                        │  │ WebSocket hub          │  │
                        │  └────────────────────────┘  │
                        └───┬───────────────────┬──────┘
                            │                   │
                  ┌─────────▼────────┐   ┌──────▼─────────┐
                  │   PostgreSQL     │   │     Redis      │
                  │ source of truth  │   │ pub/sub        │
                  │ row locks        │   │ rate limits    │
                  │ outbox           │   │ (accelerator)  │
                  └─────────▲────────┘   └──────▲─────────┘
                            │                   │
                        ┌───┴───────────────────┴──────┐
                        │  Worker  × M replicas        │
                        │  · lifecycle scheduler       │
                        │  · outbox relay              │
                        └──────────────────────────────┘
                                     │
                        ┌────────────▼─────────────────┐
                        │ Prometheus ──▶ Grafana       │
                        │ (OTel traces optional)       │
                        └──────────────────────────────┘
```

**PostgreSQL is the only source of truth.** Redis is an accelerator: lose it and
the platform still serves correct data — realtime falls back to per-process
fan-out and the rate limiter fails open. This is deliberate. A system where a
cache outage causes incorrect *auction results* is not one you want to operate.

---

## 3. The bidding engine

### 3.1 Proxy bidding, and why

Naive "highest number wins" bidding rewards whoever refreshes fastest and
punishes people who are asleep. Real auction houses — and eBay — use **proxy
bidding**: you declare a *maximum*, and the house bids on your behalf in
increments, only as high as needed to lead.

The displayed price is therefore a function of the **top two** maximums:

| Situation | Result |
| --- | --- |
| First bid | Price = start price, regardless of how high the maximum is |
| Challenger max > leader max | Challenger leads at `min(challenger_max, leader_max + increment)` |
| Challenger max ≤ leader max | Leader holds at `min(leader_max, challenger_max + increment)`; challenger is outbid instantly |
| Leader raises own max | Price does not move; only their ceiling rises |

The `min(...)` clamps are what stop the price overshooting anyone's authorised
maximum, and are the source of the subtle bugs this design avoids. The rules
live in [`services/pricing.py`](../backend/app/services/pricing.py) as **pure
functions** — no database, no clock — so they are exhaustively unit-testable and
readable by someone who is not an engineer. A property test asserts the price is
monotonic across 400 random legal bids.

When the incumbent's proxy defends the lead, the engine writes **two** ledger
entries — the challenger's bid, and the incumbent's automatic defence. This
keeps a strict invariant: *every price the auction ever displayed is explained by
a row in the ledger.* Without it, spectators would see the price jump with no
corresponding bid, which looks exactly like fraud.

### 3.2 The concurrency guarantee

This is the heart of the system.

```python
# services/bidding.py — the shape of every bid
auction = await session.execute(
    select(Auction).where(Auction.id == auction_id).with_for_update()
)   # ← every concurrent bidder on THIS auction queues here
...  # validate state, clock, price rules, deposit
...  # append the hash-chained ledger entry
...  # move the deposit holds
...  # emit outbox events
# one COMMIT: all of it, or none of it
```

**Why pessimistic locking and not optimistic retries?** Under the traffic pattern
that actually matters — a burst converging on the final seconds of *one* popular
auction — optimistic concurrency livelocks. Everyone reads the same version,
everyone collides, everyone retries, everyone collides again; throughput
collapses exactly when it is needed most. Pessimistic row locking queues bidders
fairly and each waiter re-reads fresh state. `version` is still maintained, but
as a *staleness signal for clients*, not as the write guard.

**Why this scales.** The lock is per-auction. 500 live auctions means 500
independent lock domains bidding in parallel. Throughput scales with the number
of live auctions, which is the dimension that actually grows. A single global
mutex, or serialisable isolation across the whole table, would not.

**Deadlock is impossible, not merely unlikely.** A bid may lock two deposit
accounts (incoming and outgoing leader). Two auctions could grab the same pair in
opposite order. We impose a global lock order — auction row first, then deposit
accounts by ascending `user_id` — which removes the cycle rather than hoping the
database's deadlock detector cleans up after us.

**What is asserted, after every concurrency storm:**

1. Exactly one bid is `LEADING`, and the auction points at it
2. `bid_count` equals the ledger row count; `version` equals the accepted-bid count
3. Sequences are `1..N` with no gaps, and the hash chain verifies
4. `current_price` equals the last ledger entry's amount
5. `held ≤ balance` for every account; at most one active hold per auction

See [`tests/test_concurrency.py`](../backend/tests/test_concurrency.py).

### 3.3 Soft close (anti-sniping)

A bid landing inside the anti-snipe window (default 120s) pushes `ends_at` out by
the extension (default 120s), capped at 20 extensions so an auction cannot run
forever. This is a fairness mechanism: without it, the winner is whoever has the
lowest network latency, not whoever values the bike most.

The extension happens **inside the same locked transaction as the bid**, so two
simultaneous last-second bids cannot both extend independently and cannot
disagree about the new end time.

### 3.4 The clock is authoritative, not the status column

An auction is closed when `now >= ends_at`, full stop. The scheduler makes that
durable and notifies people, but it is not what *decides* it. A bid arriving in
the gap between "due" and "swept" is rejected by `place_bid`'s own clock check
under the row lock. This is why a slow or briefly-dead worker is an availability
problem, never a correctness one.

---

## 4. The tamper-evident ledger

Each bid stores `prev_hash` (the digest of bid *n-1* in the same auction) and its
own `entry_hash`:

```
entry_hash = SHA256(prev_hash | auction_id | bidder_id | sequence
                    | amount | max_amount | placed_at)
```

Any retroactive edit, insertion, deletion or reordering changes that entry's
digest and therefore every digest after it. One pass detects tampering **and
points at where it started**. `GET /api/v1/auctions/{id}/ledger` exposes this to
anyone, and the auction page has a "Verify ledger" button.

Sequence numbers come from `auctions.last_bid_sequence`, a column on the row we
already hold a lock on — so allocation is race-free without a sequence object,
and the chain is per-auction rather than global.

**What this is and is not.** It is tamper-*evidence*: a database administrator
who rewrites history is detectable. It is not tamper-*proofing* — someone with
full write access could recompute the whole chain. Making that impossible needs
an external anchor (periodically publishing the head hash somewhere the operator
does not control). That is a genuine next step, noted in ASSUMPTIONS.md, and the
data model already supports it.

---

## 5. Realtime

### 5.1 Transactional outbox

The obvious implementation — write the bid, then publish to Redis — has a window
where the bid committed but the publish failed. Everyone watching sees a stale
price until they refresh, and there is no record that anything was missed.

Instead, events are inserted into `outbox_events` **in the same transaction** as
the state change. A relay worker then claims undispatched rows with
`FOR UPDATE SKIP LOCKED` and publishes them.

- Redis down → events queue durably in Postgres and drain on recovery
- Relay crashes mid-batch → the transaction rolls back, rows stay unclaimed
- N relay replicas → `SKIP LOCKED` partitions the work with no coordination
- `outbox_pending_events` is the single gauge that says "realtime is degraded"

Delivery is **at-least-once**; the client dedupes on the monotonic
`auction.version` and on bid `sequence`.

### 5.2 Fan-out and the WebSocket layer

Channels are per-auction (`rt:auction:{id}`) and per-user (`rt:user:{id}`), so a
client watching one auction never receives traffic for the other 499. Redis
pub/sub decouples "which replica accepted the bid" from "which replica holds the
spectator's socket" — no sticky sessions, no replica-to-replica awareness.

Sockets are **push-only**. Bids go over authenticated HTTPS so they get the full
middleware stack: rate limiting, idempotency, audit logging, structured errors.
Reimplementing all of that over a WebSocket would be duplicated surface area for
no benefit.

A slow consumer that cannot drain its queue is **dropped**, not buffered
indefinitely. One bad client must not apply backpressure to an entire auction.

### 5.3 Clock skew, and why it is not cosmetic

Every frame carries `server_time`. The client maintains
`offset = server_time − local_time` (smoothed with an EMA to absorb jitter) and
renders every deadline through it.

This matters more than it sounds. A user whose laptop clock is 40 seconds fast
would see "Ended" while bidding is still open — and would stop bidding. The
countdown in the final minute *is* the product; showing the wrong one loses
someone an auction.

---

## 6. Data model

```
users ──┬── refresh_tokens        (rotating families, reuse detection)
        ├── deposit_accounts      (balance / held, DB-enforced held ≤ balance)
        ├── deposit_holds         (partial unique index: 1 ACTIVE per auction)
        ├── deposit_transactions  (append-only money movement)
        └── notifications

bikes ───── auctions ──┬── bids   (append-only, hash-chained)
                       └── watchlist

outbox_events      (transactional outbox)
audit_logs         (who did what, from where)
idempotency_records(replay protection for unsafe endpoints)
```

**Choices worth defending:**

- **`NUMERIC(14,2)`, never float.** Money in binary floating point produces
  arguments with customers you cannot win.
- **UUID primary keys.** Auction IDs appear in URLs; sequential integers would
  leak inventory volume and make enumeration trivial.
- **Partial unique indexes as invariants.** "One open auction per bike" and "one
  active hold per (user, auction)" are enforced by PostgreSQL, not by
  application code that a future refactor could bypass. A race cannot violate
  them even in principle.
- **Check constraints for domain rules.** `ends_at > starts_at`,
  `held <= balance`, `max_amount >= amount`, `reserve >= start_price`. The
  database refuses to store nonsense regardless of which code path wrote it.
- **JSONB for inspection reports.** A 100-point checklist that evolves and is
  never queried in a hot path. Modelling it relationally would buy migrations
  and nothing else.
- **`current_price` denormalised onto `auctions`.** Deriving it from the ledger
  on every read would be correct but slow, and we already hold the row lock on
  every write. The invariant "last ledger entry == current_price" is asserted in
  tests, so the denormalisation cannot silently drift.

---

## 7. Security

| Threat | Mitigation |
| --- | --- |
| Password cracking | Argon2id (64 MiB, t=3, p=4); transparent rehash on parameter upgrade |
| Credential stuffing | Per-account lockout after 8 failures; per-IP and per-user token-bucket limits |
| User enumeration | Login and registration return identical shapes; a dummy hash equalises timing |
| Stolen refresh token | Single-use rotating tokens; replaying a spent one revokes the whole family |
| Stale sessions after password change | `token_version` claim — stateless revocation, no blocklist |
| Privilege escalation | RBAC dependency on every admin route; suspension bumps `token_version`, killing live sessions |
| SQL injection | SQLAlchemy Core throughout; no string-built SQL anywhere |
| Mass assignment | Pydantic models are explicit allow-lists; ORM objects are never populated from raw request bodies |
| Bid-history leakage | Rivals' `max_amount` never leaves the server; bidders appear as per-auction aliases |
| Replayed writes | `Idempotency-Key` with a request fingerprint; reusing a key with a different body is a loud 409 |
| Header spoofing | `X-Forwarded-For` trusted only when `TRUST_PROXY_HEADERS=true` |
| XSS / clickjacking | Strict CSP, `nosniff`, `X-Frame-Options: DENY`, HSTS in production |
| Oversized payloads | 1 MiB body cap before parsing |

**One security control deserves special mention.** Refresh-family revocation
happens in its own committed transaction, because the request-scoped session
rolls back on the exception we are about to raise — which would quietly undo the
revocation. A security control that only applies on the happy path is not a
control. This was caught by a test, not by inspection.

---

## 8. Observability

**Structured logs.** Every line is JSON in production with `request_id`,
`trace_id` and `user_id` bound via contextvars — so a support ticket ("my bid
failed at 14:32") is one log query, not an afternoon. Inbound `X-Request-ID` is
honoured so a trace spans the load balancer and the API.

**Metrics that map to user pain**, not to CPU graphs:

| Metric | The question it answers |
| --- | --- |
| `auction_bid_placement_duration_seconds` | Does bidding feel instant? |
| `auction_bid_lock_wait_seconds` | Is contention the cause, or the database? |
| `auction_bids_total{outcome}` | Are we rejecting bids we should accept? |
| `outbox_pending_events` | Is realtime silently degraded? |
| `realtime_ws_connections` | How many people are actually watching? |
| `auctions_live` | Is the scheduler alive? |
| `rate_limit_rejections_total` | Are limits protecting us or hurting users? |

Label cardinality is bounded on purpose: route *templates*, never raw paths;
outcome codes, never auction IDs.

**Health endpoints are split.** `/health` never touches a dependency, so a slow
database cannot cause the orchestrator to kill a healthy pod. `/health/ready`
checks Postgres and Redis, so a pod with a broken dependency leaves the load
balancer instead of serving errors.

**Alerts** ship in [`ops/alerts.yml`](../ops/alerts.yml), each with a description
that tells the on-call what to look at next. Traces are OpenTelemetry, opt-in via
`OTEL_ENABLED` with the SDK as an optional extra, so the base image stays slim.

---

## 9. Scaling

**What scales today, unchanged:**

- **API replicas** — stateless; WebSocket fan-out goes through Redis, so no
  sticky sessions. `make scale` runs three.
- **Workers** — `SKIP LOCKED` claiming means adding replicas adds throughput
  without coordination or leader election.
- **Read traffic** — list and detail queries select explicit column tuples and
  are covered by composite indexes; they move to a read replica with a
  connection-string change.

**Where it breaks first, and what I would do:**

| Limit | Symptom | Fix |
| --- | --- | --- |
| ~50–100 bids/sec on **one** auction | `auction_bid_lock_wait_seconds` climbs | Per-auction sharding, or an in-memory single-writer per auction with the DB as the durable log |
| Postgres write throughput | Commit latency rises | Partition `bids` by `auction_id`; move settled auctions to cold storage |
| Redis pub/sub fan-out | Dropped frames | Redis Cluster, or Kafka with per-auction partitions |
| Search over a large catalogue | Slow `ILIKE` | `pg_trgm` / `tsvector` index, then OpenSearch |
| WebSocket connections per node | Memory | A dedicated gateway tier, since sockets and request handling scale differently |

None of these are needed at the scale this assignment describes, and building
them now would be speculative complexity. They are listed because knowing *where*
a design breaks is part of the design.

---

## 10. Decision records

The four decisions that shaped everything else, with the alternatives and why
they lost:

- [ADR-0001 — Pessimistic row locking for bid placement](adr/0001-pessimistic-locking-for-bids.md)
- [ADR-0002 — Transactional outbox for realtime events](adr/0002-transactional-outbox.md)
- [ADR-0003 — Hash-chained bid ledger](adr/0003-hash-chained-bid-ledger.md)
- [ADR-0004 — Proxy bidding as the auction mechanism](adr/0004-proxy-bidding.md)
