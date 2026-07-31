"""End-to-end API tests: auth, authorisation, validation, browsing, admin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from app.db.models.enums import UserRole

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------- auth
async def test_register_login_and_access_a_protected_route(client):
    payload = {
        "email": "new.buyer@example.com",
        "password": "Str0ngPassword!",
        "full_name": "New Buyer",
    }
    registered = await client.post("/api/v1/auth/register", json=payload)
    assert registered.status_code == 201, registered.text
    body = registered.json()
    assert body["user"]["email"] == payload["email"]
    assert "password" not in registered.text

    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["full_name"] == "New Buyer"


async def test_duplicate_email_is_rejected(client):
    payload = {
        "email": "dupe@example.com",
        "password": "Str0ngPassword!",
        "full_name": "First",
    }
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    "password", ["short", "alllowercase123", "ALLUPPERCASE123", "NoDigitsHereAtAll"]
)
async def test_weak_passwords_are_rejected(client, password):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": password, "full_name": "Weak"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_wrong_password_gives_the_same_error_as_unknown_email(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "real@example.com", "password": "Str0ngPassword!", "full_name": "R"},
    )
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": "real@example.com", "password": "Wr0ngPassword!"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "Wr0ngPassword!"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"] == unknown.json()["error"], "responses must not leak existence"


async def test_refresh_rotates_and_replaying_the_old_token_kills_the_family(client):
    registered = (
        await client.post(
            "/api/v1/auth/register",
            json={"email": "rot@example.com", "password": "Str0ngPassword!", "full_name": "Rot"},
        )
    ).json()
    original = registered["refresh_token"]

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
    assert rotated.status_code == 200
    new_token = rotated.json()["refresh_token"]
    assert new_token != original

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

    # The family was burned, so even the *legitimate* newest token is dead.
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_token})
    assert after.status_code == 401


async def test_logout_all_invalidates_existing_access_tokens(client, make_user, auth_headers):
    user = await make_user()
    headers = await auth_headers(user)
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 200

    assert (await client.post("/api/v1/auth/logout-all", headers=headers)).status_code == 200

    after = await client.get("/api/v1/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "TOKEN_REVOKED"


async def test_unauthenticated_and_malformed_tokens_are_rejected(client):
    assert (await client.get("/api/v1/me")).status_code == 401
    bad = await client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "TOKEN_INVALID"


# ------------------------------------------------------------------ browsing
async def test_list_and_filter_auctions(client, make_auction):
    await make_auction(start_price=D("40000"))
    await make_auction(start_price=D("90000"))

    all_auctions = await client.get("/api/v1/auctions")
    assert all_auctions.status_code == 200
    assert all_auctions.json()["total"] == 2

    filtered = await client.get("/api/v1/auctions", params={"min_price": "80000"})
    assert filtered.json()["total"] == 1

    by_city = await client.get("/api/v1/auctions", params={"city": "bengaluru"})
    assert by_city.json()["total"] == 2

    empty = await client.get("/api/v1/auctions", params={"make": "Ducati"})
    assert empty.json()["total"] == 0


async def test_auction_detail_by_id_and_by_slug(client, make_auction):
    auction = await make_auction()
    by_id = await client.get(f"/api/v1/auctions/{auction.id}")
    by_slug = await client.get(f"/api/v1/auctions/{auction.slug}")
    assert by_id.status_code == by_slug.status_code == 200
    assert by_id.json()["id"] == by_slug.json()["id"]
    assert by_id.json()["bike"]["make"] == "Royal Enfield"


async def test_live_auction_detail_is_not_cacheable(client, make_auction):
    auction = await make_auction()
    response = await client.get(f"/api/v1/auctions/{auction.id}")
    assert response.headers["cache-control"] == "no-store"


async def test_unknown_auction_returns_a_structured_404(client):
    response = await client.get("/api/v1/auctions/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["request_id"]


# ------------------------------------------------------------------- bidding
async def test_place_a_bid_over_http(client, make_user, make_auction, auth_headers):
    auction = await make_auction(deposit_required=D("0"))
    user = await make_user(deposit=D("0"))
    response = await client.post(
        f"/api/v1/auctions/{auction.id}/bids",
        json={"max_amount": "75000"},
        headers=await auth_headers(user),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_leading"] is True
    assert body["current_price"] == "50000.00"
    assert body["verdict"] == "LEAD_TAKEN"
    assert len(body["entry_hash"]) == 64


async def test_bidding_requires_authentication(client, make_auction):
    auction = await make_auction()
    response = await client.post(
        f"/api/v1/auctions/{auction.id}/bids", json={"max_amount": "75000"}
    )
    assert response.status_code == 401


async def test_stale_version_guard_rejects_a_bid_built_on_old_state(
    client, make_user, make_auction, auth_headers
):
    auction = await make_auction(deposit_required=D("0"))
    a, b = await make_user(deposit=D("0")), await make_user(deposit=D("0"))
    await client.post(
        f"/api/v1/auctions/{auction.id}/bids",
        json={"max_amount": "60000"},
        headers=await auth_headers(a),
    )
    stale = await client.post(
        f"/api/v1/auctions/{auction.id}/bids",
        json={"max_amount": "90000", "expected_version": 0},
        headers=await auth_headers(b),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_VERSION"


async def test_bid_history_hides_private_maximums(client, make_user, make_auction, auth_headers):
    auction = await make_auction(deposit_required=D("0"))
    a, b = await make_user(deposit=D("0")), await make_user(deposit=D("0"))
    await client.post(
        f"/api/v1/auctions/{auction.id}/bids",
        json={"max_amount": "150000"},
        headers=await auth_headers(a),
    )
    await client.post(
        f"/api/v1/auctions/{auction.id}/bids",
        json={"max_amount": "60000"},
        headers=await auth_headers(b),
    )

    history = await client.get(f"/api/v1/auctions/{auction.id}/bids")
    assert history.status_code == 200
    assert "150000" not in history.text, "a rival's ceiling must never be exposed"
    for entry in history.json():
        assert "max_amount" not in entry
        assert entry["bidder_alias"].startswith("Bidder-")


async def test_ledger_endpoint_verifies_the_chain(client, make_user, make_auction, auth_headers):
    auction = await make_auction(deposit_required=D("0"))
    user = await make_user(deposit=D("0"))
    await client.post(
        f"/api/v1/auctions/{auction.id}/bids",
        json={"max_amount": "75000"},
        headers=await auth_headers(user),
    )
    verdict = await client.get(f"/api/v1/auctions/{auction.id}/ledger")
    assert verdict.status_code == 200
    assert verdict.json()["valid"] is True
    assert verdict.json()["entries_checked"] == 1


# ----------------------------------------------------------------- watchlist
async def test_watch_and_unwatch(client, make_user, make_auction, auth_headers):
    auction = await make_auction()
    user = await make_user()
    headers = await auth_headers(user)

    assert (
        await client.put(f"/api/v1/auctions/{auction.id}/watch", headers=headers)
    ).status_code == 200
    assert (await client.get("/api/v1/me/watchlist", headers=headers)).json()["total"] == 1
    # Idempotent: watching twice is not an error and does not duplicate.
    await client.put(f"/api/v1/auctions/{auction.id}/watch", headers=headers)
    assert (await client.get("/api/v1/me/watchlist", headers=headers)).json()["total"] == 1

    assert (
        await client.delete(f"/api/v1/auctions/{auction.id}/watch", headers=headers)
    ).status_code == 200
    assert (await client.get("/api/v1/me/watchlist", headers=headers)).json()["total"] == 0


# ------------------------------------------------------------------ deposits
async def test_top_up_and_withdraw_respect_held_funds(
    client, make_user, make_auction, auth_headers
):
    auction = await make_auction(deposit_required=D("5000"))
    user = await make_user(deposit=D("0"))
    headers = await auth_headers(user)

    topped = await client.post(
        "/api/v1/me/deposit/top-up", json={"amount": "10000"}, headers=headers
    )
    assert topped.json()["available"] == "10000.00"

    await client.post(
        f"/api/v1/auctions/{auction.id}/bids", json={"max_amount": "75000"}, headers=headers
    )
    balance = (await client.get("/api/v1/me/deposit", headers=headers)).json()
    assert balance["held"] == "5000.00"
    assert balance["available"] == "5000.00"

    over = await client.post(
        "/api/v1/me/deposit/withdraw", json={"amount": "8000"}, headers=headers
    )
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "INSUFFICIENT_AVAILABLE_BALANCE"


# --------------------------------------------------------------------- admin
async def test_buyers_cannot_reach_admin_routes(client, make_user, auth_headers):
    user = await make_user()
    response = await client.get("/api/v1/admin/stats", headers=await auth_headers(user))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_admin_can_create_a_bike_and_schedule_an_auction(client, make_user, auth_headers):
    admin = await make_user(role=UserRole.ADMIN)
    headers = await auth_headers(admin)

    bike = await client.post(
        "/api/v1/admin/bikes",
        headers=headers,
        json={
            "registration_number": "ka01ab1234",
            "make": "KTM",
            "model": "Duke 390",
            "year": 2022,
            "engine_cc": 373,
            "odometer_km": 12000,
            "city": "Mumbai",
            "condition_grade": "A",
            "inspection_score": 93,
            "estimated_value": "230000",
            "images": ["https://example.com/1.jpg"],
        },
    )
    assert bike.status_code == 201, bike.text
    assert bike.json()["registration_number"] == "KA01AB1234", "should be normalised"

    now = datetime.now(timezone.utc)
    auction = await client.post(
        "/api/v1/admin/auctions",
        headers=headers,
        json={
            "bike_id": bike.json()["id"],
            "starts_at": (now + timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(hours=6)).isoformat(),
            "start_price": "165000",
            "bid_increment": "1000",
            "reserve_price": "200000",
            "deposit_required": "5000",
        },
    )
    assert auction.status_code == 201, auction.text
    assert auction.json()["status"] == "SCHEDULED"
    assert auction.json()["has_reserve"] is True

    audit = await client.get("/api/v1/admin/audit", headers=headers)
    actions = {entry["action"] for entry in audit.json()}
    assert {"bike.created", "auction.created"} <= actions


async def test_admin_can_cancel_an_auction_and_release_deposits(
    client, make_user, make_auction, auth_headers
):
    auction = await make_auction(deposit_required=D("5000"))
    buyer = await make_user()
    admin = await make_user(role=UserRole.ADMIN)

    await client.post(
        f"/api/v1/auctions/{auction.id}/bids",
        json={"max_amount": "75000"},
        headers=await auth_headers(buyer),
    )
    cancelled = await client.post(
        f"/api/v1/admin/auctions/{auction.id}/cancel",
        json={"reason": "Registration papers under dispute"},
        headers=await auth_headers(admin),
    )
    assert cancelled.status_code == 200

    balance = (await client.get("/api/v1/me/deposit", headers=await auth_headers(buyer))).json()
    assert balance["held"] == "0.00"


async def test_suspending_a_user_kills_their_live_session(client, make_user, auth_headers):
    buyer = await make_user()
    admin = await make_user(role=UserRole.ADMIN)
    buyer_headers = await auth_headers(buyer)
    assert (await client.get("/api/v1/me", headers=buyer_headers)).status_code == 200

    await client.patch(
        f"/api/v1/admin/users/{buyer.id}",
        json={"status": "SUSPENDED"},
        headers=await auth_headers(admin),
    )
    after = await client.get("/api/v1/me", headers=buyer_headers)
    assert after.status_code == 401


# ----------------------------------------------------------------------- ops
async def test_health_and_readiness(client):
    assert (await client.get("/health")).json()["status"] == "ok"
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"]["status"] == "ok"


async def test_metrics_are_exposed_in_prometheus_format(client, make_auction):
    await client.get("/api/v1/auctions")
    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    assert "http_requests_total" in metrics.text
    assert "auction_bid_placement_duration_seconds" in metrics.text


async def test_security_headers_and_request_id_are_present(client):
    response = await client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]


async def test_inbound_request_id_is_propagated(client):
    response = await client.get("/health", headers={"X-Request-ID": "trace-me-123"})
    assert response.headers["x-request-id"] == "trace-me-123"


async def test_openapi_document_is_valid(client):
    schema = (await client.get("/openapi.json")).json()
    assert schema["info"]["title"]
    assert "/api/v1/auctions/{auction_id}/bids" in schema["paths"]
