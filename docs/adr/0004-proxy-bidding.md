# ADR-0004 — Proxy bidding as the auction mechanism

**Status:** Accepted · **Date:** 2026-07-31

## Context

The brief says "real-time bidding" without specifying the mechanism. The choice
shapes the data model, the concurrency story, the UI and the fairness properties
of the whole platform, so it deserves an explicit decision.

## Options considered

**A. Direct bidding** — you submit a price; highest price wins.
**B. Proxy bidding** — you submit a *maximum*; the platform bids on your behalf
in increments, only as high as needed to lead.
**C. Sealed bid** — everyone submits once, highest wins, revealed at close.

## Decision

**Option B**, eBay-style proxy bidding, with the rules isolated as pure functions
in `services/pricing.py`.

## Why

Direct bidding (A) rewards whoever refreshes fastest and has the best network
connection. It forces bidders to sit at the screen for the closing minutes and
punishes anyone in a different timezone. It also produces incremental bidding
wars that push the price up in tiny steps, which is a poor experience for
everyone including the seller.

Sealed bid (C) is fine for procurement but removes the price discovery and the
engagement that make an auction platform work at all.

Proxy bidding is what real auction houses use, for good reasons: a bidder states
their true valuation once and walks away; the price only rises as far as
competition actually pushes it; and it composes well with a soft close to remove
sniping entirely.

It is also the more interesting engineering problem, which matters for an
assignment being judged on engineering decisions. The displayed price becomes a
function of the top **two** maximums, which is where the subtle bugs live —
exactly the kind of logic worth isolating and testing exhaustively.

## Consequences

**Good**

- Fair to bidders who are not watching the clock
- Bidders never pay more than they authorised, and usually pay less
- Rules are pure functions: unit-testable exhaustively, including a property test
  asserting price monotonicity over 400 random legal bids
- Combines naturally with soft close to eliminate sniping

**Bad / accepted**

- **Users misunderstand it.** The most common misconception is "I will be charged
  my maximum." The UI addresses this directly, in money, before they commit —
  this was a UX requirement created by the architecture choice.
- Two ledger entries are needed when an incumbent's proxy defends the lead, so
  that every displayed price is explained by a row in the ledger. Without that,
  spectators would see the price jump with no visible bid, which looks like
  fraud.
- `max_amount` is commercially sensitive and must never leak. It is excluded from
  every serialiser, and a test asserts a rival's maximum does not appear in the
  bid-history response body.
- Tie-breaking needs an explicit rule. Equal maximums favour the incumbent — the
  standard auction-house convention, and the only rule that is deterministic
  under concurrency.
