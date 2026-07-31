import { Link } from 'react-router-dom'
import { compactMoney, number } from '../lib/format'
import type { AuctionSummary } from '../lib/types'
import { Countdown } from './Countdown'
import { StatusChip } from './ui'

export function AuctionCard({
  auction,
  remaining,
}: {
  auction: AuctionSummary
  remaining: (iso: string) => number
}) {
  const isLive = auction.status === 'LIVE'
  const deadline = isLive ? auction.ends_at : auction.starts_at

  return (
    <Link
      to={`/auctions/${auction.slug}`}
      className="card group overflow-hidden transition-shadow hover:shadow-lg focus-visible:shadow-lg"
    >
      <div className="relative aspect-[4/3] overflow-hidden bg-ink-100 dark:bg-ink-800">
        {auction.thumbnail ? (
          <img
            src={auction.thumbnail}
            alt={auction.title}
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="grid h-full place-items-center text-4xl text-ink-400">🏍</div>
        )}
        <div className="absolute left-3 top-3 flex gap-2">
          <StatusChip status={auction.status} outcome={auction.outcome} />
        </div>
        {auction.has_reserve && !auction.reserve_met && isLive && (
          <span className="chip absolute right-3 top-3 bg-black/60 text-white backdrop-blur">
            Reserve not met
          </span>
        )}
      </div>

      <div className="space-y-3 p-4">
        <div>
          <h3 className="line-clamp-1 font-semibold">{auction.title}</h3>
          <p className="mt-0.5 text-xs text-ink-500">
            {auction.city} · {number(auction.bid_count)} bids · {number(auction.bidder_count)} bidders
          </p>
        </div>

        <div className="flex items-end justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-500">
              {auction.bid_count > 0 ? 'Current bid' : 'Starting at'}
            </p>
            <p className="text-lg font-bold tabular">{compactMoney(auction.current_price)}</p>
          </div>
          <div className="text-right text-sm">
            {!isLive && auction.status === 'SCHEDULED' && (
              <span className="text-xs text-ink-500">starts in </span>
            )}
            {auction.status === 'ENDED' || auction.status === 'CANCELLED' ? (
              <span className="text-xs text-ink-500">
                {auction.outcome === 'SOLD' ? 'Sold' : 'No sale'}
              </span>
            ) : (
              /* List cards tick every 5s: 20 cards × 1s would be 20 renders a
                 second for information nobody reads that precisely. */
              <Countdown
                endsAt={deadline}
                remaining={remaining}
                intervalMs={5000}
                showLabel={false}
              />
            )}
          </div>
        </div>
      </div>
    </Link>
  )
}
