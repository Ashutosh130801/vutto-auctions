# Deployment

This document describes a **production** deployment. If you just want the
project live on a public URL for free, see
[DEPLOY_FREE.md](DEPLOY_FREE.md) instead — Neon + Render + Cloudflare Pages,
about 20 minutes, no credit card.

## 1. Local / demo

```bash
make up      # build, migrate, seed, start everything
make logs    # tail api + worker
make scale   # 3 API replicas, to show horizontal scaling works
make down    # stop (keeps data)
make clean   # stop and delete data
```

| Service | Port | Notes |
| --- | --- | --- |
| `web` (nginx + SPA) | 8080 | Proxies `/api` to `api`, including the WebSocket upgrade |
| `api` | 8000 | FastAPI; `/docs`, `/health`, `/health/ready`, `/metrics` |
| `worker` | — | Lifecycle scheduler + outbox relay |
| `postgres` | 5432 | Source of truth |
| `redis` | 6379 | Pub/sub + rate limits |
| `prometheus` | 9090 | Scrapes `api` via DNS SD, so `make scale` is picked up automatically |
| `grafana` | 3001 | Dashboard pre-provisioned |

The `migrate` service runs `alembic upgrade head && python -m app.seed` and then
exits. `api` and `worker` declare
`depends_on: { migrate: { condition: service_completed_successfully } }`, so
there is no first-boot race and no "wait for the database" sleep anywhere.

---

## 2. Configuration

Everything is environment-driven; see [`.env.example`](../.env.example).
Settings are validated at import, so a misconfigured deployment fails at boot
rather than at the first request.

**Must be set in production:**

| Variable | Why |
| --- | --- |
| `SEED_ON_START` | Set `false` in production. The container entrypoint seeds demo data otherwise (it is a no-op once any user exists, but be explicit) |
| `SECRET_KEY` | Signs JWTs and keys the refresh-token digest. `python -c "import secrets;print(secrets.token_urlsafe(64))"`. Boot **fails** if it still looks like the dev default. |
| `APP_ENV=production` | Enables HSTS and the secret-strength check |
| `LOG_FORMAT=json` | Machine-parseable logs |
| `DATABASE_URL` | Point at managed Postgres |
| `REDIS_URL` | Point at managed Redis |
| `CORS_ORIGINS` | Your real origins only |
| `TRUST_PROXY_HEADERS=true` | **Only** when genuinely behind your own proxy — otherwise clients spoof `X-Forwarded-For` and bypass IP rate limits |
| `RUN_BACKGROUND_WORKERS=false` | On API pods; the worker deployment owns them |

Rotating `SECRET_KEY` invalidates all sessions by design. To rotate without a
mass logout, run both keys for one refresh-token TTL.

---

## 3. Production topology

```
        Internet
            │  TLS terminates here
      ┌─────▼──────┐
      │  Ingress   │  (ALB / nginx / Cloudflare)
      └──┬──────┬──┘
         │      │
   ┌─────▼──┐ ┌─▼──────────────┐
   │ web ×2 │ │  api  × 3..N   │  stateless, no sticky sessions
   └────────┘ └─┬────────────┬─┘
                │            │
       ┌────────▼───┐  ┌─────▼──────┐     ┌──────────────┐
       │ PostgreSQL │  │   Redis    │     │ worker × 2   │
       │ primary    │  │ (managed)  │◀────┤ scheduler +  │
       │  + replica │  └────────────┘     │ outbox relay │
       └────────────┘                     └──────────────┘
```

**Sizing to start:** API `0.5 vCPU / 512 MiB` per replica, worker the same,
Postgres 2 vCPU / 8 GiB with 200 max connections, Redis 1 GiB.

**Why this scales cleanly:**

- API pods are stateless — WebSocket fan-out goes through Redis, so no sticky
  sessions and no draining concerns beyond normal connection close.
- Workers claim rows with `FOR UPDATE SKIP LOCKED`, so adding replicas adds
  throughput with no leader election and no split-brain.
- Running 1 or 10 of either is a replica-count change and nothing else.

---

## 4. Deploying a change

```bash
# 1. Build and push immutable, digest-pinned images
docker build -t registry/vutto-api:$SHA ./backend
docker build -t registry/vutto-web:$SHA ./frontend --build-arg VITE_API_BASE_URL=""
docker push registry/vutto-api:$SHA && docker push registry/vutto-web:$SHA

# 2. Migrate FIRST, as a job, before any new pod starts
docker run --rm -e DATABASE_URL=$DATABASE_URL registry/vutto-api:$SHA \
  alembic upgrade head

# 3. Roll the API, then the workers, then the web tier
```

**Migrations must be backwards-compatible with the currently running version**,
because during a rolling deploy both versions are live. The expand/contract
pattern:

1. **Expand** — add the nullable column / new table. Deploy. Old code ignores it.
2. **Migrate** — backfill, and deploy code that writes both old and new.
3. **Contract** — deploy code that reads only the new shape. Then drop the old
   column in a *later* release.

Never combine a destructive migration with the deploy that stops using the
column. That is the difference between a rollback being one command and being an
incident.

**Rollback:** redeploy the previous image digest. Because migrations are
backwards-compatible, no database rollback is needed — which is the point of the
discipline above.

---

## 5. Backups and recovery

| What | How | Target |
| --- | --- | --- |
| Postgres | Managed automated backups + PITR/WAL archiving | RPO 5 min, RTO 1 h |
| Redis | **No backup needed** — pub/sub is transient, rate-limit buckets are disposable | — |
| Images | Registry retention, digest-pinned | — |

Restore drill (run it quarterly; an untested backup is a hope, not a backup):

```bash
pg_restore -d $NEW_DATABASE_URL backup.dump
docker run --rm -e DATABASE_URL=$NEW_DATABASE_URL registry/vutto-api:$SHA \
  alembic current            # confirm the schema version matches the image
```

**Verifying integrity after a restore** is a genuine advantage of the hash chain:
call `GET /api/v1/auctions/{id}/ledger` on a sample of auctions. If the chain
verifies, no bid was lost or corrupted in transit.

---

## 6. Monitoring

Prometheus scrapes `/metrics` from every API replica (DNS service discovery, so
scaling needs no config change). Grafana is provisioned with the
**Vutto Auctions — platform health** dashboard.

Alerts live in [`ops/alerts.yml`](../ops/alerts.yml). Each maps to a user-visible
failure and says what to check next:

| Alert | Severity | Means |
| --- | --- | --- |
| `ApiErrorRate` | critical | 5xx above 2% — users are seeing failures |
| `OutboxBacklogGrowing` | critical | Realtime is stale; **no data lost**, check Redis and the worker |
| `BidLatencyHigh` | warning | p99 bid > 500 ms; compare against `bid_lock_wait` |
| `BidLockContention` | warning | One auction is hot; contention, not the database |
| `BidRejectionSpike` | warning | Usually a stale client minimum or too-tight limits |
| `NoLiveAuctions` | info | Is the scheduler running? |

**SLOs to run against:**

| Objective | Target |
| --- | --- |
| Bid placement p99 | < 300 ms |
| Realtime propagation p95 | < 1 s |
| API availability | 99.9% |
| Auction closes within its scheduled second | 99.99% |

---

## 7. Runbook

**"Bids are slow."**
Check `auction_bid_lock_wait_seconds` first. If it dominates
`auction_bid_placement_duration_seconds`, this is contention on one hot auction,
not a database problem — the queue is doing its job and the fix is sharding, not
a bigger instance. If lock wait is low but total is high, look at database CPU
and connection-pool saturation.

**"Prices are not updating live."**
Check `outbox_pending_events`. If it is climbing, the relay cannot publish —
check Redis reachability and worker logs. **No data is lost**; the backlog drains
automatically once Redis returns. If the gauge is zero, the problem is on the
socket side: check `realtime_ws_connections` and the ingress WebSocket timeout
(it must exceed the auction duration; nginx is set to 3600s).

**"An auction did not close on time."**
Check that at least one worker is running and `auctions_live` is being updated.
Note that this is an availability issue, not a correctness one: bids after
`ends_at` are rejected by the engine's own clock check regardless of the
scheduler. Force-close from the admin console if needed.

**"A bidder disputes the history."**
`GET /api/v1/auctions/{id}/ledger`. A valid chain is cryptographic evidence that
no bid was altered, inserted or removed. Cross-reference `audit_logs` for any
administrative action on that auction.

**"Someone's deposit is stuck."**
Check `deposit_holds` for `ACTIVE` rows on ended auctions. Closing and
cancellation both settle holds transactionally, so a stuck hold means an auction
never reached a terminal state — find it and close it from the admin console.

**"Suspected credential compromise."**
`PATCH /api/v1/admin/users/{id}` with `status: SUSPENDED` bumps `token_version`,
which invalidates every live access token immediately and revokes all refresh
tokens. Grep the logs for `auth.refresh_reuse_detected` — that event means a
refresh token was replayed and the family was automatically burned.

---

## 8. Security checklist before going live

- [ ] `SECRET_KEY` generated fresh, stored in a secrets manager, never in git
- [ ] `APP_ENV=production` (enables HSTS and the secret-strength check)
- [ ] TLS terminated at the ingress; HTTP redirects to HTTPS
- [ ] `CORS_ORIGINS` restricted to real origins
- [ ] `TRUST_PROXY_HEADERS=true` **only** behind your own proxy
- [ ] `/metrics` not reachable from the internet (the nginx config denies it)
- [ ] Database credentials rotated off the compose defaults
- [ ] Postgres reachable only from the application subnet
- [ ] Seed data **not** loaded in production (`app.seed` no-ops if users exist)
- [ ] Rate limits reviewed against expected legitimate traffic
- [ ] Backup restore drill completed and documented
