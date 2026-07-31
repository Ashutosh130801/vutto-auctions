import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { useServerClock } from '../hooks/useServerClock'
import type { AuctionSummary, Page } from '../lib/types'
import { AuctionCard } from '../components/AuctionCard'
import { SkeletonCard } from '../components/ui'

export function Home() {
  const clock = useServerClock()
  const [live, setLive] = useState<AuctionSummary[] | null>(null)

  useEffect(() => {
    api.get<Page<AuctionSummary>>('/api/v1/auctions?status=LIVE&sort=ending_soon&page_size=8', { auth: false })
      .then((page) => setLive(page.items))
      .catch(() => setLive([]))
  }, [])

  return (
    <div className="space-y-10">
      <section className="card overflow-hidden">
        <div className="grid items-center gap-8 p-8 md:grid-cols-2 md:p-12">
          <div className="space-y-5">
            <span className="chip bg-brand-500/15 text-brand-600 dark:text-brand-400">
              Inspected · Graded · Auctioned
            </span>
            <h1 className="text-3xl font-extrabold leading-tight tracking-tight sm:text-4xl">
              Bid on used motorcycles you can actually trust.
            </h1>
            <p className="text-ink-500">
              Set the maximum you would pay. We bid for you in small steps and stop the
              instant you are in front — and late bids extend the clock, so nobody wins
              by sniping in the last second.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link to="/auctions" className="btn-primary px-6 py-3">Browse live auctions</Link>
              <Link to="/register" className="btn-ghost px-6 py-3">Create an account</Link>
            </div>
          </div>
          <ul className="space-y-3 text-sm">
            {[
              ['Proxy bidding', 'You never pay more than you authorised, and rarely that much.'],
              ['Soft close', 'A bid in the final minutes extends the auction for everyone.'],
              ['Verifiable history', 'Every bid is hash-chained; anyone can re-verify the ledger.'],
              ['Refundable deposit', 'Held only while you lead. Released the moment you are outbid.'],
            ].map(([title, body]) => (
              <li key={title} className="rounded-xl border border-ink-200 p-4 dark:border-ink-800">
                <p className="font-semibold">{title}</p>
                <p className="mt-1 text-ink-500">{body}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="space-y-4">
        <div className="flex items-end justify-between">
          <h2 className="text-xl font-bold tracking-tight">Ending soon</h2>
          <Link to="/auctions" className="text-sm font-semibold text-brand-500 hover:underline">
            See all →
          </Link>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {live === null
            ? Array.from({ length: 4 }, (_, i) => <SkeletonCard key={i} />)
            : live.map((a) => <AuctionCard key={a.id} auction={a} remaining={clock.remaining} />)}
        </div>
      </section>
    </div>
  )
}
