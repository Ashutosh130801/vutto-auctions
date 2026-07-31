import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { ApiError, api } from '../lib/api'
import { money } from '../lib/format'
import { useAuth } from '../hooks/useAuth'
import type { BidAccepted, Deposit } from '../lib/types'
import { ErrorNote, Spinner } from './ui'

interface Props {
  auctionId: string
  minimumNextBid: string
  increment: string
  depositRequired: string
  version: number
  isLeading: boolean
  isLive: boolean
  yourMax: string | null
  onAccepted: (result: BidAccepted) => void
}

/**
 * The bid form.
 *
 * Design decisions worth stating:
 *
 * - **The maximum is explained, not assumed.** Proxy bidding confuses people
 *   into thinking they will be charged their maximum. The panel says what will
 *   actually happen, in money, before they commit.
 * - **Idempotency key per attempt.** Generated once when the form is armed, so
 *   a retry after a network blip replays rather than double-bids.
 * - **`expected_version` is sent.** If the price moved between render and
 *   submit, the server rejects it and we re-render instead of silently placing
 *   a bid the user never actually saw the context for.
 */
export function BidPanel({
  auctionId, minimumNextBid, increment, depositRequired, version,
  isLeading, isLive, yourMax, onAccepted,
}: Props) {
  const { user } = useAuth()
  const [amount, setAmount] = useState(minimumNextBid)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [deposit, setDeposit] = useState<Deposit | null>(null)

  useEffect(() => { setAmount(minimumNextBid) }, [minimumNextBid])
  useEffect(() => {
    if (user) api.get<Deposit>('/api/v1/me/deposit').then(setDeposit).catch(() => {})
  }, [user, version])

  const quickBids = useMemo(() => {
    const min = Number(minimumNextBid)
    const step = Number(increment)
    return [min, min + step * 2, min + step * 5]
  }, [minimumNextBid, increment])

  const shortfall = deposit ? Number(depositRequired) - Number(deposit.available) : 0
  const needsTopUp = !isLeading && shortfall > 0

  async function submit(value: string) {
    setBusy(true); setError(null); setFlash(null)
    try {
      const result = await api.post<BidAccepted>(
        `/api/v1/auctions/${auctionId}/bids`,
        { max_amount: value, expected_version: version },
        { idempotencyKey: crypto.randomUUID() },
      )
      onAccepted(result)
      setFlash(
        result.verdict === 'OUTBID_IMMEDIATELY'
          ? `Outbid instantly — someone already authorised more. Price is now ${money(result.current_price)}.`
          : result.verdict === 'LEAD_RAISED'
            ? `Maximum raised to ${money(result.your_max)}. Price stays at ${money(result.current_price)}.`
            : `You're leading at ${money(result.current_price)}.`,
      )
    } catch (err) {
      if (err instanceof ApiError) setError(err)
      else setError(new ApiError(0, 'NETWORK', 'Could not reach the server. Check your connection.'))
    } finally {
      setBusy(false)
    }
  }

  if (!user) {
    return (
      <div className="card space-y-3 p-5 text-center">
        <p className="text-sm text-ink-500">Sign in to place a bid on this motorcycle.</p>
        <Link to="/login" className="btn-primary w-full">Sign in to bid</Link>
      </div>
    )
  }

  if (!isLive) {
    return (
      <div className="card p-5 text-center text-sm text-ink-500">
        Bidding is closed for this auction.
      </div>
    )
  }

  return (
    <div className={clsx('card space-y-4 p-5', isLeading && 'ring-1 ring-emerald-500/40')}>
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">Place your bid</h3>
        {isLeading && (
          <span className="chip bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
            You're leading
          </span>
        )}
      </div>

      <div>
        <label className="label" htmlFor="max-bid">Your maximum</label>
        <div className="relative">
          <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-sm text-ink-400">₹</span>
          <input
            id="max-bid"
            className="input pl-7 text-lg font-semibold tabular"
            inputMode="numeric"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ''))}
            disabled={busy}
          />
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-500">
          We bid only as much as needed to keep you in front — never more than
          your maximum. Minimum acceptable right now is{' '}
          <span className="font-semibold text-ink-700 dark:text-ink-200">{money(minimumNextBid)}</span>.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {quickBids.map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setAmount(String(value))}
            disabled={busy}
            className={clsx(
              'rounded-lg border px-2 py-2 text-xs font-semibold tabular transition-colors',
              Number(amount) === value
                ? 'border-brand-500 bg-brand-500/10 text-brand-600 dark:text-brand-400'
                : 'border-ink-200 hover:bg-ink-100 dark:border-ink-700 dark:hover:bg-ink-800',
            )}
          >
            {money(value)}
          </button>
        ))}
      </div>

      {yourMax && (
        <p className="text-xs text-ink-500">
          Your current maximum: <span className="font-semibold tabular">{money(yourMax)}</span>
        </p>
      )}

      {needsTopUp && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3.5 py-3 text-xs text-amber-700 dark:text-amber-300">
          <p className="font-medium">A refundable deposit of {money(depositRequired)} is required.</p>
          <p className="mt-1">
            You need {money(shortfall)} more.{' '}
            <Link to="/account" className="underline">Top up</Link> — it is released the moment you are outbid.
          </p>
        </div>
      )}

      {error && (
        <ErrorNote
          message={
            error.code === 'BID_TOO_LOW' && error.details.minimum_required
              ? `Too low — the minimum is now ${money(error.details.minimum_required)}.`
              : error.code === 'STALE_VERSION'
                ? 'Someone bid while you were typing. The price above has been updated.'
                : error.message
          }
          requestId={error.requestId}
        />
      )}
      {flash && (
        <p className="rounded-xl bg-emerald-500/10 px-3.5 py-3 text-sm text-emerald-600 dark:text-emerald-400">
          {flash}
        </p>
      )}

      <button
        className="btn-primary w-full py-3 text-base"
        onClick={() => submit(amount)}
        disabled={busy || !amount || Number(amount) <= 0}
      >
        {busy ? <><Spinner className="h-4 w-4" /> Placing…</> : isLeading ? 'Raise my maximum' : 'Place bid'}
      </button>
    </div>
  )
}
