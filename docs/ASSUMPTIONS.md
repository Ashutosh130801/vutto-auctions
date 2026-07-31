# Assumptions and trade-offs

The brief says reasonable assumptions are free to make. Here is every one I
made, why, and what I would change with more time. I have tried to be honest
about the weak spots rather than only listing the good decisions.

---

## 1. Domain assumptions

| # | Assumption | Reasoning |
| --- | --- | --- |
| 1 | **Timed auctions, not continuous/live-auctioneer.** Each has a fixed start and end. | Matches how used-vehicle marketplaces actually operate online. A live auctioneer model would need a very different UX and a human in the loop. |
| 2 | **Proxy bidding, not raw highest-bid.** | Raw bidding rewards whoever refreshes fastest. Proxy bidding is what real auction houses use, is fairer to bidders who are asleep, and is the more interesting engineering problem. |
| 3 | **Platform-owned inventory** (Vutto buys, inspects, then auctions). Sellers are modelled but not exposed. | Keeps the trust story coherent — every bike carries a platform inspection grade. Adding a seller portal is additive, not structural. |
| 4 | **A refundable security deposit gates bidding.** Held while you lead, released on outbid, captured on win. | This is how real vehicle auctions stop frivolous bids. It also creates the interesting money-conservation invariants that the concurrency tests assert. |
| 5 | **Optional reserve price, hidden from bidders.** Bidders see only "reserve met / not met". | Standard auction practice. Revealing it would let bidders stop exactly at it. |
| 6 | **Soft close: 2-minute window, 2-minute extension, 20 extensions max.** | Long enough to respond, capped so an auction cannot run forever. Configurable per auction. |
| 7 | **One live auction per bike**, enforced by a partial unique index. | An unsold bike can be relisted; it cannot be in two auctions at once. |
| 8 | **INR, single currency, no tax/fees.** | Multi-currency is a large, well-understood problem that would add volume without adding insight. `NUMERIC(14,2)` throughout means adding it later is a schema change, not a rewrite. |
| 9 | **Ties favour the incumbent.** Equal maximums leave the earlier bidder leading. | The standard auction-house rule, and the only one that is deterministic under concurrency. |

---

## 2. Deliberate simplifications

These are places where the *mechanism* is built and enforced, but the external
integration is stubbed. Each is a one-adapter change, not a redesign.

| Area | What is real | What is stubbed | What production needs |
| --- | --- | --- | --- |
| **KYC / verification** | The `kyc_verified` gate is enforced on every bid path, admins can grant/revoke it, and revocation kills live sessions | Registration auto-verifies | Document upload, a review queue, a provider integration |
| **Payments** | Full deposit ledger: balance, holds, capture, release, append-only transaction log, DB-enforced `held ≤ balance` | `POST /me/deposit/top-up` credits directly | Replace with a signed PSP webhook. A client should never be able to credit its own balance. **This endpoint would not exist in production.** |
| **Notifications** | Persisted, delivered live over WebSocket, shown in the UI | No email or SMS | An outbound adapter consuming the same outbox — the events are already there |
| **Images** | Stored as URL lists, rendered in a gallery | Deterministic placeholder URLs | Presigned S3 uploads, a CDN, and image validation |
| **Settlement** | Winner is recorded, deposit captured, bike marked sold | No invoice or logistics | Invoicing, delivery scheduling, ownership transfer |
| **Email verification** | — | Not implemented | Standard double-opt-in |

---

## 3. Trade-offs, with the alternative I rejected

### Pessimistic row locking over optimistic concurrency
**Chosen:** `SELECT ... FOR UPDATE` on the auction row.
**Rejected:** version-check-and-retry.
**Why:** under a burst on one hot auction, optimistic retries livelock — everyone
collides, retries, collides again — precisely when the system matters most.
Pessimistic locking queues bidders fairly. The cost is that bids on one auction
serialise, which caps single-auction throughput at roughly 50–100/sec. That is
far beyond what this platform needs, and the lock is per-auction so aggregate
throughput scales with the number of live auctions.
[ADR-0001](adr/0001-pessimistic-locking-for-bids.md)

### Transactional outbox over direct publish
**Chosen:** write events to Postgres in the bid's transaction; a relay publishes.
**Rejected:** publishing to Redis inline.
**Why:** direct publish has a window where the bid committed but nobody was told,
with no record that anything was missed. The outbox costs one extra table, a
small write and up to ~250 ms of relay latency. In exchange, realtime failure is
always recoverable and always visible as a single gauge.
[ADR-0002](adr/0002-transactional-outbox.md)

### Redis as an accelerator, never a dependency
**Chosen:** the app boots and serves correctly with Redis down.
**Why:** a cache outage must never change *auction results*. The cost is
degraded behaviour that has to be understood: single-node realtime fan-out and a
rate limiter that fails open. Both are logged loudly at startup rather than
failing silently.

### Denormalised `current_price` on the auction row
**Chosen:** maintain it, and assert the invariant in tests.
**Rejected:** derive it from the ledger on every read.
**Why:** deriving is more obviously correct but adds an aggregate to every list
query. Since every write already holds the row lock, maintaining it is free. The
test `test_last_ledger_entry_always_equals_the_live_price` is what stops the
denormalisation drifting.

### WebSocket token as a query parameter
**Chosen:** `?token=<access_token>` on the socket URL.
**Why:** browsers cannot set headers on a WebSocket handshake. Mitigations: the
token is short-lived (15 min), the socket is strictly read-only, and bids still
go over authenticated HTTPS.
**Residual risk:** the token may appear in proxy access logs. Production fix: a
separate single-use, socket-scoped ticket issued by a REST call — roughly 30
lines, deliberately left out to avoid unexplained ceremony in a review build.

### Offset pagination for auctions, keyset for the ledger
**Chosen:** both, each where it fits.
**Why:** auction lists are small, users jump to arbitrary pages and want total
counts — offset is right. The bid ledger is unbounded and append-only, so it is
paginated by `sequence`, which is stable under concurrent inserts.

### `ILIKE` search rather than full-text
**Chosen:** `ILIKE '%term%'` across title, make, model and registration.
**Why:** correct and adequate for a catalogue of this size. It will not scale —
the migration path (`pg_trgm`, then a `tsvector` column, then OpenSearch) is
noted in ARCHITECTURE.md. Building it now would be speculative.

### In-process workers *and* a worker container
**Chosen:** both, with `SKIP LOCKED` making the duplication harmless.
**Why:** `docker compose up` works with one command and no ordering gotchas,
while production runs the dedicated container and sets
`RUN_BACKGROUND_WORKERS=false` on the API. Convenience locally, separation in
production, no divergent code paths.

---

## 4. Things I chose not to build

Listed so their absence reads as a decision rather than an oversight.

- **Auto-bid scheduling / sniping tools for users** — actively hostile to the
  anti-snipe design.
- **A seller-facing portal** — additive; would have added volume, not insight.
- **Multi-currency and tax** — well-understood, large, not interesting here.
- **Elasticsearch** — premature at this catalogue size.
- **Kubernetes manifests** — DEPLOYMENT.md covers the topology and the scaling
  properties; committing YAML for a cluster nobody will run is theatre.
- **E2E browser tests** — the backend suite covers correctness where the risk
  actually is. Playwright would have been the next thing I added.
- **gRPC / GraphQL** — REST plus WebSocket is the right fit for this shape.

---

## 5. Known weaknesses

Being straight about these matters more to me than the feature list.

1. **Single-auction write throughput is capped** by design (~50–100 bids/sec).
   Fine here; would need per-auction sharding at a much larger scale.
2. **Tamper-evidence is not tamper-proofing.** An operator with full write access
   could recompute the entire chain. Real integrity needs an external anchor —
   publishing the head hash somewhere the operator does not control. The data
   model already supports it; the anchoring job is not written.
3. **No frontend unit tests.** Type checking and linting are enforced in CI and
   the backend is well covered (126 tests, ~85%), but `useAuctionStream`'s
   reconnect and version-guard logic is only exercised by hand. This is the
   biggest remaining gap and the first thing I would close.
4. **The relay polls** every 250 ms rather than using `LISTEN/NOTIFY`. Simpler
   and more robust to restarts, but it adds a little latency and idle load.
5. **Deposit top-up is a client-callable endpoint.** Documented above, but worth
   repeating: this must become a PSP webhook before anything resembling real
   money is involved.
6. **`bidder_count` is maintained incrementally** rather than derived. Cheap, and
   invariant-tested — but a bug in the increment path would drift silently. A
   periodic reconciliation job would be the belt-and-braces fix.
7. **No rate limiting on WebSocket connections per IP.** A client could open many
   sockets. Bounded per-connection queues limit the damage, but a connection cap
   belongs at the ingress.

---

## 6. What I would do next, in order

1. **Frontend tests** for the realtime hook (reconnect, out-of-order frames,
   version guard) and the bid panel.
2. **Replace the deposit top-up endpoint** with a signed PSP webhook.
3. **Anchor the ledger** — publish the head hash on an interval to make the
   integrity claim hold against the operator.
4. **`LISTEN/NOTIFY`** to cut outbox relay latency to near zero.
5. **Playwright E2E** for the two-bidder anti-snipe scenario, which is the single
   highest-value user journey.
6. **Read replica** for browse and detail traffic.
7. **`pg_trgm` search index** once the catalogue outgrows `ILIKE`.
