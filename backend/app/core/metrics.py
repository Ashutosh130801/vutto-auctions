"""Prometheus instrumentation.

We deliberately keep cardinality low: labels are bounded sets (route *template*,
method, status class, outcome code) — never raw paths, user ids or auction ids.
"""

from __future__ import annotations

import os

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, multiprocess

REGISTRY = CollectorRegistry()
if os.getenv("PROMETHEUS_MULTIPROC_DIR"):  # pragma: no cover - gunicorn deployments
    multiprocess.MultiProcessCollector(REGISTRY)

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests processed.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

bids_total = Counter(
    "auction_bids_total",
    "Bid attempts by outcome.",
    ["outcome"],  # accepted | rejected_low | rejected_state | rejected_deposit | error
    registry=REGISTRY,
)
bid_placement_duration_seconds = Histogram(
    "auction_bid_placement_duration_seconds",
    "End-to-end latency of the atomic bid transaction.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    registry=REGISTRY,
)
bid_lock_wait_seconds = Histogram(
    "auction_bid_lock_wait_seconds",
    "Time spent waiting on the per-auction row lock — the contention signal.",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
    registry=REGISTRY,
)
anti_snipe_extensions_total = Counter(
    "auction_anti_snipe_extensions_total",
    "Soft-close extensions granted.",
    registry=REGISTRY,
)
auctions_live = Gauge(
    "auctions_live",
    "Auctions currently in the LIVE state.",
    registry=REGISTRY,
)
auction_transitions_total = Counter(
    "auction_transitions_total",
    "Auction lifecycle transitions.",
    ["to_status"],
    registry=REGISTRY,
)

ws_connections = Gauge(
    "realtime_ws_connections",
    "Open WebSocket connections on this process.",
    registry=REGISTRY,
)
ws_messages_sent_total = Counter(
    "realtime_ws_messages_sent_total",
    "Frames pushed to clients.",
    ["event"],
    registry=REGISTRY,
)

outbox_pending = Gauge(
    "outbox_pending_events",
    "Undispatched rows in the transactional outbox — the reliability signal.",
    registry=REGISTRY,
)
outbox_dispatched_total = Counter(
    "outbox_dispatched_total",
    "Outbox events relayed to the realtime bus.",
    ["event_type"],
    registry=REGISTRY,
)

rate_limit_rejections_total = Counter(
    "rate_limit_rejections_total",
    "Requests rejected by the token-bucket limiter.",
    ["scope"],
    registry=REGISTRY,
)
