# Vutto Auctions

A production-grade live auction platform for used motorcycles: multiple
simultaneous auctions, real-time proxy bidding, soft-close anti-sniping, a
tamper-evident bid ledger, and the observability you would actually want at
3 a.m. when a popular auction is closing.

Built as a Software Engineering Internship assignment. The interesting parts are
the concurrency guarantees around bidding, the realtime architecture, and the
operational story — not the feature count.

---

## Run it

**One command.** Docker and Docker Compose v2 are the only prerequisites.

```bash
git clone <this-repo> && cd vutto
make up
```

That builds the images, runs migrations, seeds a realistic demo dataset, and
starts everything:

| What | Where |
| --- | --- |
| **Application** | <http://localhost:8080> |
| Interactive API docs | <http://localhost:8000/docs> |
| Prometheus | <http://localhost:9090> |
| Grafana (`admin` / `admin`) | <http://localhost:3001> |
| Raw metrics | <http://localhost:8000/metrics> |

**Sign in with:**

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@vutto.example.com` | `Admin@12345` |
| Buyer | `aarav@vutto.example.com` | `Demo@12345` |

Also seeded: `diya@`, `rohan@`, `ishita@`, `kabir@` (same password). Each buyer
starts with a ₹50,000 refundable deposit.

> **Try this first.** One seeded auction closes about three minutes after you
> start the stack. Open it in two browser windows signed in as two different
> buyers, and bid in the last minute — you will watch the anti-snipe extension
> fire and both windows update in real time.

`make down` stops it; `make clean` also deletes the data.

**Want it on a public URL?** [docs/DEPLOY_FREE.md](docs/DEPLOY_FREE.md) walks
through a genuinely free deployment (Neon + Render + Cloudflare Pages) in about
20 minutes, no credit card.

---

## Run it without Docker

<details>
<summary>Local development setup</summary>

Requires Python 3.11+, Node 20+, PostgreSQL 15+ and (optionally) Redis.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://vutto:vutto@localhost:5432/vutto
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload            # http://localhost:8000

# Frontend, in a second terminal
cd frontend
npm install
npm run dev                              # http://localhost:5173
```

Redis is optional locally. Without it the app runs single-node: realtime still
works within one process, and the rate limiter fails open. The startup log says
so explicitly rather than failing silently.

</details>

---

## What it does

**For buyers**

- Browse and filter live, upcoming and finished auctions
- A live auction room with sub-second price updates, viewer presence, and a
  countdown corrected for your device's clock skew
- **Proxy bidding** — enter the most you would pay; the engine bids the minimum
  needed to keep you in front and stops there
- Refundable deposits, held only while you lead and released the moment you are outbid
- Watchlist, bid history, outbid notifications
- **Verify the bid ledger yourself** from the auction page

**For operators**

- Inventory management with inspection grading
- Auction scheduling with reserve price, increment, deposit and anti-snipe policy
- Force-close and cancel (which releases every deposit)
- User suspension that terminates live sessions immediately
- Full audit trail of every administrative action
- Metrics, alerts and a provisioned Grafana dashboard

---

## The engineering, briefly

Full detail in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**. The short version:

**Bidding is race-free by construction.** Every bid runs in one transaction that
opens with `SELECT ... FOR UPDATE` on the auction row. Concurrent bidders on the
same auction serialise; different auctions stay fully parallel. The auction row,
the ledger entry, the deposit holds and the outbound events are all written in
that same transaction, so partial states cannot exist.
[→ the test that proves it](backend/tests/test_concurrency.py)

**Realtime survives failure.** Domain events go into a transactional outbox in
the same commit as the state change, then a relay publishes them to Redis for
WebSocket fan-out. If Redis dies, events queue in Postgres and drain on
recovery — realtime degrades, data does not.

**The bid history is tamper-evident.** Bids are hash-chained: each entry commits
to the previous one. `GET /api/v1/auctions/{id}/ledger` recomputes the chain and
reports the exact sequence number where any alteration begins.

**Auction closing needs no singleton.** Lifecycle workers claim due auctions with
`FOR UPDATE SKIP LOCKED`, so every replica runs the same loop safely. There is
no cron to fail over.

**Sessions are revocable without a blocklist.** Short-lived access tokens carry a
`token_version`; refresh tokens are single-use and rotate. Replaying a spent
refresh token revokes the whole token family — a stolen credential becomes a
loud, bounded incident instead of a silent, permanent one.

---

## Testing

```bash
make test          # everything, against a real PostgreSQL
make test-fast     # skip the concurrency storms
make test-cov      # with coverage
```

**126 tests, ~85% line coverage, ~30 seconds.** Coverage on the parts that matter:
`pricing` 100%, `bidding` 94%, `auction` 93%, `auth` 92%, models 100%.

The suite runs against **real PostgreSQL**, never SQLite — the entire
correctness argument rests on row locking, `SKIP LOCKED`, partial unique indexes
and `NUMERIC` semantics, none of which SQLite reproduces. A green suite on
SQLite would be actively misleading. If `TEST_DATABASE_URL` is unset the harness
boots a throwaway PostgreSQL automatically, so a bare `pytest` works with no
infrastructure at all.

| Suite | What it covers |
| --- | --- |
| `test_pricing` | Proxy-bid rules as pure functions, incl. a property test asserting price monotonicity over 400 random legal bids |
| `test_bidding_engine` | The engine against a real database: proxy semantics, deposits, anti-snipe, closing, ledger tampering |
| `test_concurrency` | Hundreds of genuinely parallel bids on real connections |
| `test_auth_security` | Lockout, suspension, token reuse, forged and expired JWTs, `typ` confusion |
| `test_realtime` | Outbox atomicity and relay idempotence |
| `test_websocket` | The live socket protocol end to end, including that private maximums never appear in a frame |
| `test_api` | HTTP contract, authorisation, validation, error envelope |
| `test_seed` | That the demo a reviewer runs actually works |
| `test_dburl` | Connection-string normalisation, so a pasted Neon/Supabase URL works verbatim |

The concurrency tests assert five invariants after every storm: exactly one
leader, no lost updates, a contiguous and cryptographically intact ledger, a
price explained by that ledger, and conserved money.

---

## Repository layout

```
backend/
  app/
    core/        config, security, logging, metrics, tracing, rate limiting, deps
    db/models/   SQLAlchemy models — the domain
    services/    business logic: pricing, bidding, auction lifecycle, auth
    api/v1/      HTTP + WebSocket routes
    realtime/    Redis pub/sub bus and the WebSocket manager
    workers/     lifecycle scheduler and outbox relay
  migrations/    Alembic
  tests/         pytest, incl. the concurrency suite
frontend/
  src/
    pages/       Browse, AuctionRoom, Account, Admin, Auth
    components/  UI, bid panel, countdown, cards
    hooks/       auth, auction stream, server clock
    lib/         API client, types, formatting
ops/             Prometheus config, alert rules, Grafana provisioning
docs/            architecture, deployment, assumptions, API, ADRs
```

---

## Documentation

| Document | What is in it |
| --- | --- |
| [DEPLOY_FREE.md](docs/DEPLOY_FREE.md) | Get it live on a public URL for ₹0, no credit card — ~20 minutes |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, the concurrency model, data model, realtime, scaling |
| [ASSUMPTIONS.md](docs/ASSUMPTIONS.md) | Every assumption made, every trade-off taken, and what I would do next |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production deployment, scaling, backups, runbook |
| [API.md](docs/API.md) | Endpoint reference, error codes, idempotency, WebSocket protocol |
| [adr/](docs/adr/) | Architecture decision records for the four decisions that mattered |
