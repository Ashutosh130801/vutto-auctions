import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import clsx from 'clsx'
import { api } from '../lib/api'
import { datetime, money, number, relative } from '../lib/format'
import { useAuth } from '../hooks/useAuth'
import { useAuctionStream } from '../hooks/useAuctionStream'
import { useServerClock } from '../hooks/useServerClock'
import type { AuctionDetail, Bid, LedgerVerdict } from '../lib/types'
import { Countdown } from '../components/Countdown'
import { BidPanel } from '../components/BidPanel'
import { Empty, GradeBadge, Spinner, StatusChip } from '../components/ui'

export function AuctionRoom() {
  const { slug = '' } = useParams()
  const { user } = useAuth()
  const clock = useServerClock()

  const [auction, setAuction] = useState<AuctionDetail | null>(null)
  const [bids, setBids] = useState<Bid[]>([])
  const [activeImage, setActiveImage] = useState(0)
  const [extendedBanner, setExtendedBanner] = useState<string | null>(null)
  const [outbidBanner, setOutbidBanner] = useState<string | null>(null)
  const [ledger, setLedger] = useState<LedgerVerdict | null>(null)
  const [loading, setLoading] = useState(true)
  const flashRef = useRef<HTMLSpanElement>(null)

  const load = useCallback(async () => {
    const detail = await api.get<AuctionDetail>(`/api/v1/auctions/${slug}`)
    setAuction(detail)
    setBids(await api.get<Bid[]>(`/api/v1/auctions/${detail.id}/bids?limit=60`))
    return detail
  }, [slug])

  useEffect(() => { void load().finally(() => setLoading(false)) }, [load])

  const stream = useAuctionStream({
    auctionId: auction?.id ?? '',
    onServerTime: clock.sync,
    onBids: (incoming) => {
      // De-duplicate by sequence: frames are at-least-once.
      setBids((prev) => {
        const seen = new Set(prev.map((b) => b.sequence))
        const fresh = incoming
          .filter((b) => !seen.has(b.sequence))
          .map((b) => ({ ...b, is_you: b.bidder_id === user?.id }))
        return [...fresh.reverse(), ...prev].slice(0, 80)
      })
      flashRef.current?.classList.remove('animate-flash')
      void flashRef.current?.offsetWidth   // force reflow so the animation restarts
      flashRef.current?.classList.add('animate-flash')
    },
    onExtended: (endsAt) => {
      setExtendedBanner(`A late bid extended this auction to ${datetime(endsAt)}.`)
      window.setTimeout(() => setExtendedBanner(null), 12_000)
    },
    onOutbid: (payload) => {
      setOutbidBanner(`You've been outbid — the price is now ${money(payload.current_price)}.`)
      window.setTimeout(() => setOutbidBanner(null), 15_000)
    },
    onEnded: () => { void load() },
  })

  // A reconnect means we may have missed frames; the socket is an accelerator,
  // the API is the source of truth.
  useEffect(() => {
    if (stream.reconnectCount > 0) void load()
  }, [stream.reconnectCount, load])

  const live = stream.live
  const price = live?.currentPrice ?? auction?.current_price ?? '0'
  const minimumNext = live?.minimumNextBid ?? auction?.minimum_next_bid ?? '0'
  const endsAt = live?.endsAt ?? auction?.ends_at ?? new Date().toISOString()
  const status = (live?.status ?? auction?.status ?? 'LIVE') as AuctionDetail['status']
  const version = live?.version ?? auction?.version ?? 0
  const isLeading = Boolean(user && live?.leadingUserId === user.id) || Boolean(auction?.you_are_leading && !live)
  const isLive = status === 'LIVE' && clock.remaining(endsAt) > 0
  const closingSoon = isLive && clock.remaining(endsAt) < (auction?.anti_snipe_window_seconds ?? 120) * 1000

  const yourMax = useMemo(() => {
    const mine = bids.filter((b) => b.is_you)
    return mine.length ? mine[0]!.amount : auction?.your_max_bid ?? null
  }, [bids, auction])

  if (loading) {
    return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8 text-brand-500" /></div>
  }
  if (!auction) return <Empty title="Auction not found" hint="It may have been removed." />

  const bike = auction.bike

  return (
    <div className="space-y-5">
      {/* Live banners --------------------------------------------------- */}
      {outbidBanner && (
        <div role="status" className="animate-slide-up rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm font-medium text-rose-600 dark:text-rose-300">
          {outbidBanner}
        </div>
      )}
      {extendedBanner && (
        <div role="status" className="animate-slide-up rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm font-medium text-amber-700 dark:text-amber-300">
          ⏱ {extendedBanner}
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[1.6fr_1fr]">
        {/* Left column ------------------------------------------------- */}
        <div className="space-y-5">
          <div className="card overflow-hidden">
            <div className="relative aspect-[16/10] bg-ink-100 dark:bg-ink-800">
              {bike.images[activeImage] ? (
                <img src={bike.images[activeImage]} alt={`${auction.title} photo ${activeImage + 1}`} className="h-full w-full object-cover" />
              ) : (
                <div className="grid h-full place-items-center text-6xl text-ink-400">🏍</div>
              )}
              <div className="absolute left-4 top-4 flex flex-wrap gap-2">
                <StatusChip status={status} outcome={auction.outcome} />
                <GradeBadge grade={bike.condition_grade} score={bike.inspection_score} />
              </div>
              {stream.viewers > 0 && (
                <span className="chip absolute right-4 top-4 bg-black/60 text-white backdrop-blur">
                  👁 {number(stream.viewers)} watching
                </span>
              )}
            </div>
            {bike.images.length > 1 && (
              <div className="flex gap-2 overflow-x-auto p-3">
                {bike.images.map((src, i) => (
                  <button
                    key={src}
                    onClick={() => setActiveImage(i)}
                    aria-label={`View photo ${i + 1}`}
                    className={clsx(
                      'h-16 w-24 shrink-0 overflow-hidden rounded-lg border-2 transition-colors',
                      i === activeImage ? 'border-brand-500' : 'border-transparent opacity-60 hover:opacity-100',
                    )}
                  >
                    <img src={src} alt="" className="h-full w-full object-cover" />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Specs */}
          <section className="card p-5">
            <h2 className="mb-4 font-semibold">Specification</h2>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
              {([
                ['Year', bike.year], ['Odometer', `${number(bike.odometer_km)} km`],
                ['Engine', bike.engine_cc ? `${bike.engine_cc} cc` : 'Electric'],
                ['Owners', bike.owners_count], ['City', bike.city], ['Colour', bike.colour ?? '—'],
                ['Fuel', bike.fuel_type], ['Registration', bike.registration_number],
                ['Market estimate', money(bike.estimated_value)],
              ] as const).map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-ink-500">{label}</dt>
                  <dd className="font-medium">{value}</dd>
                </div>
              ))}
            </dl>
            {bike.description && <p className="mt-4 text-sm leading-relaxed text-ink-500">{bike.description}</p>}
          </section>

          {/* Bid history */}
          <section className="card">
            <div className="flex items-center justify-between border-b border-ink-200 p-4 dark:border-ink-800">
              <h2 className="font-semibold">Bid history</h2>
              <button
                className="text-xs font-semibold text-brand-500 hover:underline"
                onClick={async () => setLedger(await api.get<LedgerVerdict>(`/api/v1/auctions/${auction.id}/ledger`))}
              >
                Verify ledger
              </button>
            </div>

            {ledger && (
              <div className={clsx(
                'mx-4 mt-4 rounded-xl px-3.5 py-3 text-xs',
                ledger.valid
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                  : 'bg-rose-500/10 text-rose-600 dark:text-rose-400',
              )}>
                {ledger.valid ? (
                  <>
                    <p className="font-semibold">✓ Ledger intact — {ledger.entries_checked} entries verified.</p>
                    <p className="mt-1 break-all font-mono opacity-70">head {ledger.head_hash?.slice(0, 32)}…</p>
                    <p className="mt-1 opacity-70">
                      Each bid is hash-chained to the one before it, so no bid can be altered,
                      inserted or removed after the fact without breaking the chain.
                    </p>
                  </>
                ) : (
                  <p className="font-semibold">
                    ✗ Chain broken at #{ledger.broken_at_sequence} — {ledger.reason}
                  </p>
                )}
              </div>
            )}

            <div className="max-h-96 divide-y divide-ink-200 overflow-y-auto dark:divide-ink-800">
              {bids.length === 0 ? (
                <p className="p-8 text-center text-sm text-ink-500">
                  No bids yet — be the first.
                </p>
              ) : bids.map((bid) => (
                <div key={bid.id} className={clsx('flex items-center gap-3 px-4 py-3', bid.is_you && 'bg-brand-500/5')}>
                  <span className="w-8 shrink-0 text-xs text-ink-400 tabular">#{bid.sequence}</span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {bid.is_you ? 'You' : bid.bidder_alias}
                      {bid.source === 'PROXY' && (
                        <span className="ml-1.5 text-[11px] font-normal text-ink-400" title="Placed automatically by the proxy engine defending an existing maximum">
                          auto
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-ink-500">{relative(bid.placed_at)}</p>
                  </div>
                  <span className="text-sm font-semibold tabular">{money(bid.amount)}</span>
                  {bid.status === 'LEADING' && <span className="chip bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">Leading</span>}
                  {bid.status === 'WON' && <span className="chip bg-brand-500/15 text-brand-500">Won</span>}
                </div>
              ))}
            </div>
          </section>
        </div>

        {/* Right column ------------------------------------------------ */}
        <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
          <div className={clsx('card p-5', closingSoon && 'animate-pulse-ring')}>
            <div className="flex items-start justify-between gap-3">
              <h1 className="text-lg font-bold leading-snug">{auction.title}</h1>
              <span
                className={clsx(
                  'chip shrink-0',
                  stream.status === 'open'
                    ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                    : 'bg-amber-500/15 text-amber-600',
                )}
                title={stream.status === 'open' ? 'Receiving live updates' : 'Reconnecting to the live feed'}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-current" />
                {stream.status === 'open' ? 'Live' : 'Reconnecting'}
              </span>
            </div>

            <div className="mt-5">
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                {(live?.bidCount ?? auction.bid_count) > 0 ? 'Current bid' : 'Starting price'}
              </p>
              <span ref={flashRef} className="mt-1 block rounded-lg text-3xl font-extrabold tabular">
                {money(price)}
              </span>
              <p className="mt-1 text-xs text-ink-500">
                {number(live?.bidCount ?? auction.bid_count)} bids from{' '}
                {number(live?.bidderCount ?? auction.bidder_count)} bidders
              </p>
            </div>

            {auction.has_reserve && (
              <p className={clsx(
                'mt-3 text-xs font-medium',
                (live?.reserveMet ?? auction.reserve_met) ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400',
              )}>
                {(live?.reserveMet ?? auction.reserve_met) ? '✓ Reserve met' : 'Reserve not yet met'}
              </p>
            )}

            <div className="mt-4 flex items-center justify-between border-t border-ink-200 pt-4 dark:border-ink-800">
              <span className="text-xs text-ink-500">
                {status === 'SCHEDULED' ? 'Starts' : status === 'LIVE' ? 'Closes' : 'Closed'}
              </span>
              {status === 'LIVE' ? (
                <Countdown endsAt={endsAt} remaining={clock.remaining} className="text-lg" showLabel={false} />
              ) : (
                <span className="text-sm font-medium">
                  {datetime(status === 'SCHEDULED' ? auction.starts_at : auction.closed_at ?? auction.ends_at)}
                </span>
              )}
            </div>

            {auction.extension_count > 0 && (
              <p className="mt-2 text-[11px] text-ink-500">
                Extended {auction.extension_count}× by late bids
                {' '}(cap {auction.anti_snipe_max_extensions}).
              </p>
            )}
          </div>

          <BidPanel
            auctionId={auction.id}
            minimumNextBid={minimumNext}
            increment={auction.bid_increment}
            depositRequired={auction.deposit_required}
            version={version}
            isLeading={isLeading}
            isLive={isLive}
            yourMax={yourMax}
            onAccepted={(result) => {
              stream.merge({
                currentPrice: result.current_price,
                minimumNextBid: result.minimum_next_bid,
                endsAt: result.ends_at,
                version: result.auction_version,
                leadingUserId: result.is_leading ? user?.id ?? null : null,
                reserveMet: result.reserve_met,
              })
              void load()
            }}
          />

          {status === 'ENDED' && (
            <div className="card p-5 text-sm">
              <p className="font-semibold">
                {auction.outcome === 'SOLD' ? `Sold for ${money(auction.winning_amount)}` :
                 auction.outcome === 'RESERVE_NOT_MET' ? 'Closed below reserve — not sold' :
                 auction.outcome === 'NO_BIDS' ? 'Closed with no bids' : 'Cancelled'}
              </p>
            </div>
          )}

          <div className="card space-y-2 p-5 text-xs leading-relaxed text-ink-500">
            <p className="font-semibold text-ink-700 dark:text-ink-200">How bidding works here</p>
            <p>
              Enter the <strong>most</strong> you would pay. We bid on your behalf in
              {' '}{money(auction.bid_increment)} steps and stop the moment you are in front.
            </p>
            <p>
              A bid in the last {Math.round(auction.anti_snipe_window_seconds / 60)} minutes
              pushes the close out by {Math.round(auction.anti_snipe_extension_seconds / 60)} minutes,
              so nobody can win by sniping.
            </p>
            <p>
              {money(auction.deposit_required)} of your deposit is held only while you lead,
              and released the instant you are outbid.
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}
