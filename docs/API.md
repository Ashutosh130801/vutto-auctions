# API reference

Base URL `/api/v1`. Interactive docs at `/docs` (Swagger) and `/redoc`.
The OpenAPI document is at `/openapi.json` and is generated from the code, so it
cannot drift.

---

## Conventions

**Authentication** — `Authorization: Bearer <access_token>`. Access tokens last
15 minutes; refresh tokens rotate and last 30 days.

**Errors** — one envelope, always:

```json
{
  "error": {
    "code": "BID_TOO_LOW",
    "message": "Minimum acceptable bid is 51000.00.",
    "details": { "minimum_required": "51000.00", "current_price": "50000.00" }
  },
  "request_id": "9f2c1e7a8b3d4f56"
}
```

Branch on `code`. Never parse `message` — it is free to be reworded.
`request_id` is echoed in the `X-Request-ID` header and appears on every log line
for that request, so quoting it in a bug report is enough to find the trace.

**Money** is a decimal string (`"51000.00"`), never a float.

**Idempotency** — send `Idempotency-Key: <uuid>` on unsafe requests. A repeat
with the same key and the same body replays the original response verbatim. A
repeat with the same key but a *different* body is a `409 IDEMPOTENCY_KEY_REUSED`
— that is a client bug and failing loudly beats returning the wrong cached answer.

---

## Error codes

| Code | HTTP | Meaning |
| --- | --- | --- |
| `VALIDATION_ERROR` | 422 | Body/query failed validation; `details.fields[]` is per-field |
| `UNAUTHENTICATED` | 401 | Missing or absent credentials |
| `INVALID_CREDENTIALS` | 401 | Wrong email or password (identical for unknown emails, by design) |
| `TOKEN_EXPIRED` | 401 | Access token expired — refresh |
| `TOKEN_INVALID` | 401 | Malformed or unverifiable token |
| `TOKEN_REVOKED` | 401 | Password changed or sessions revoked since issue |
| `REFRESH_TOKEN_REUSED` | 401 | A spent refresh token was replayed; **the whole family is revoked** |
| `ACCOUNT_LOCKED` | 403 | Too many failed logins; `details.until` |
| `ACCOUNT_SUSPENDED` | 403 | Suspended by an administrator |
| `ACCOUNT_NOT_VERIFIED` | 403 | KYC required before bidding |
| `FORBIDDEN` | 403 | Insufficient role |
| `NOT_FOUND` | 404 | No such resource |
| `EMAIL_TAKEN` | 409 | Email already registered |
| `AUCTION_NOT_LIVE` | 409 | Not accepting bids; `details.status` |
| `AUCTION_ENDED` | 409 | Already closed; `details.ended_at` |
| `BID_TOO_LOW` | 409 | `details.minimum_required` |
| `INSUFFICIENT_DEPOSIT` | 409 | `details.required`, `available`, `shortfall` |
| `STALE_VERSION` | 409 | The auction moved on; re-render before resubmitting |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Same key, different body |
| `INSUFFICIENT_AVAILABLE_BALANCE` | 422 | Cannot withdraw funds held against a leading bid |
| `PAYLOAD_TOO_LARGE` | 413 | Body over 1 MiB |
| `RATE_LIMITED` | 429 | `details.retry_after_seconds` |
| `INTERNAL_ERROR` | 500 | Quote the `request_id` |

---

## Endpoints

### Auth

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| `POST` | `/auth/register` | — | Creates a buyer and signs in. Password ≥ 10 chars with upper, lower and digit |
| `POST` | `/auth/login` | — | Returns a token pair |
| `POST` | `/auth/refresh` | — | Rotates. Replaying a spent token revokes the family |
| `POST` | `/auth/logout` | — | Revokes one refresh token |
| `POST` | `/auth/logout-all` | ✔ | Revokes every session and invalidates live access tokens |
| `POST` | `/auth/change-password` | ✔ | Also revokes every session |

### Auctions (public)

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/auctions` | Filter by `status`, `search`, `city`, `make`, `min_price`, `max_price`, `min_year`, `max_year`. Sort by `ending_soon` (default), `newest`, `price_asc`, `price_desc`, `most_bids`. Paginated |
| `GET` | `/auctions/{ident}` | UUID **or** slug. Authenticated callers also get `your_max_bid`, `you_are_leading`, `you_are_watching` |
| `GET` | `/auctions/{id}/bids` | Newest first, keyset by `sequence`. Aliased bidders; **no `max_amount`** |
| `GET` | `/auctions/{id}/ledger` | Re-verifies the hash chain |

### Bidding

```http
POST /api/v1/auctions/{auction_id}/bids
Authorization: Bearer <token>
Idempotency-Key: 8f14e45f-ea0d-4b2b-9c7e-1c2d3e4f5a6b
Content-Type: application/json

{ "max_amount": "75000", "expected_version": 12 }
```

`max_amount` is the **maximum you authorise**, not the price you pay. The engine
bids only as high as needed to lead.

`expected_version` is optional. If supplied and the auction has moved on, the bid
is rejected with `STALE_VERSION` so the client can re-render before the user
commits to a number they never actually saw the context for.

```json
201 Created
{
  "bid_id": "…", "sequence": 7, "verdict": "LEAD_TAKEN",
  "is_leading": true, "current_price": "61000.00",
  "minimum_next_bid": "62000.00", "your_max": "75000.00",
  "extended": false, "ends_at": "2026-08-01T12:00:00+00:00",
  "reserve_met": true, "auction_version": 13,
  "entry_hash": "a3f1…"
}
```

| `verdict` | Meaning |
| --- | --- |
| `LEAD_TAKEN` | You are now the leading bidder |
| `LEAD_RAISED` | You already led; your ceiling rose, the price did not move |
| `OUTBID_IMMEDIATELY` | Someone had authorised more; their proxy defended and you are already outbid |

### Watchlist

| Method | Path |
| --- | --- |
| `PUT` | `/auctions/{id}/watch` |
| `DELETE` | `/auctions/{id}/watch` |

### Me

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/me` | Profile |
| `GET` | `/me/deposit` | `balance`, `held`, `available` |
| `POST` | `/me/deposit/top-up` | **Simulated.** Production replaces this with a PSP webhook |
| `POST` | `/me/deposit/withdraw` | Rejects amounts covered by an active hold |
| `GET` | `/me/bids` | Auctions you have bid on |
| `GET` | `/me/watchlist` | Watched auctions |
| `GET` | `/me/notifications` | `?unread_only=true` |
| `POST` | `/me/notifications/read` | Mark all read |

### Admin (role `ADMIN`)

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/admin/stats` | Operations overview |
| `POST` | `/admin/bikes` | Add inventory |
| `GET` | `/admin/bikes` | Filter by status, search |
| `PATCH` | `/admin/bikes/{id}` | Update |
| `POST` | `/admin/auctions` | Schedule |
| `POST` | `/admin/auctions/{id}/close` | Force-close; normal outcome rules apply |
| `POST` | `/admin/auctions/{id}/cancel` | Cancel and release every deposit |
| `GET` | `/admin/users` | List / search |
| `PATCH` | `/admin/users/{id}` | Status and KYC. Suspension kills live sessions |
| `GET` | `/admin/audit` | Audit trail |

Every mutating admin action writes an `audit_logs` row **in the same
transaction** as the change, so the trail cannot drift from reality.

### Ops

| Path | Purpose |
| --- | --- |
| `GET /health` | Liveness — never touches a dependency |
| `GET /health/ready` | Readiness — checks Postgres and Redis; `503` when not ready |
| `GET /metrics` | Prometheus exposition |

---

## WebSocket

```
ws://host/api/v1/auctions/{auction_id}/stream?token=<access_token>
```

The token is optional; without it you get public frames but no personal
`user.outbid` events. It is a query parameter because browsers cannot set headers
on a WebSocket handshake — see ASSUMPTIONS.md for the mitigations.

Every frame:

```json
{ "event": "auction.bid_placed", "data": { … }, "server_time": "2026-08-01T11:59:58.123456+00:00" }
```

| Event | When | Payload |
| --- | --- | --- |
| `snapshot` | On connect | Full auction state + `viewers` |
| `auction.bid_placed` | A bid was accepted | `auction` state + the new `bids[]` |
| `auction.extended` | Anti-snipe fired | `auction` + new `ends_at` |
| `auction.ended` | Auction closed | `auction`, `outcome`, `winner_id`, `winning_amount` |
| `auction.cancelled` | Cancelled by an admin | `auction`, `reason` |
| `user.outbid` | *You* were outbid (authenticated only) | Auction ref + new price |
| `presence` | Someone joined or left | `viewers` |
| `heartbeat` | Every 15 s | `viewers` — also keeps `server_time` fresh |

**Client contract.** Delivery is at-least-once and ordering is best-effort. Treat
every frame as idempotent and **drop any frame whose `auction.version` is not
greater than what you have already rendered** — that is what makes duplicates and
reordering harmless. On reconnect, refetch over HTTP: the socket is an
accelerator, the database is the truth.

Send `{"type":"ping"}` to get a `pong`. The socket is otherwise push-only; bids
go over HTTPS so they get rate limiting, idempotency and audit logging.

---

## Rate limits

Token bucket, refilling continuously (so straddling a window boundary does not
buy a double burst). Keyed per user, or per IP when anonymous.

| Scope | Default |
| --- | --- |
| `bid` | 30 / min |
| `auth` | 10 / min |
| default | 300 / min |

Exceeding one returns `429` with `details.retry_after_seconds`. If Redis is
unavailable the limiter **fails open** and logs a warning — the limiter must not
be able to take the platform down.
