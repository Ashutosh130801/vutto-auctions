import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { useServerClock } from '../hooks/useServerClock'
import type { AuctionSummary, Page } from '../lib/types'
import { AuctionCard } from '../components/AuctionCard'
import { Empty, SkeletonCard } from '../components/ui'

const SORTS = [
  ['ending_soon', 'Ending soon'],
  ['newest', 'Newest'],
  ['price_asc', 'Price: low to high'],
  ['price_desc', 'Price: high to low'],
  ['most_bids', 'Most bids'],
] as const

const STATUSES = [
  ['LIVE', 'Live now'],
  ['SCHEDULED', 'Upcoming'],
  ['ENDED', 'Ended'],
] as const

export function Browse() {
  const [params, setParams] = useSearchParams()
  const clock = useServerClock()
  const [data, setData] = useState<Page<AuctionSummary> | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchDraft, setSearchDraft] = useState(params.get('search') ?? '')

  const query = useMemo(() => {
    const q = new URLSearchParams()
    q.set('status', params.get('status') ?? 'LIVE')
    q.set('sort', params.get('sort') ?? 'ending_soon')
    q.set('page', params.get('page') ?? '1')
    q.set('page_size', '24')
    for (const key of ['search', 'city', 'make', 'min_price', 'max_price'] as const) {
      const value = params.get(key)
      if (value) q.set(key, value)
    }
    return q
  }, [params])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.get<Page<AuctionSummary>>(`/api/v1/auctions?${query}`, { auth: false }))
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => { void load() }, [load])

  // Live prices change without us doing anything; a slow poll keeps the grid
  // honest without opening 24 WebSockets.
  useEffect(() => {
    if (query.get('status') !== 'LIVE') return
    const id = window.setInterval(() => { void load() }, 20_000)
    return () => window.clearInterval(id)
  }, [load, query])

  // Debounce so we do not fire a request per keystroke.
  useEffect(() => {
    const id = window.setTimeout(() => {
      const next = new URLSearchParams(params)
      if (searchDraft) next.set('search', searchDraft)
      else next.delete('search')
      next.delete('page')
      if (next.toString() !== params.toString()) setParams(next, { replace: true })
    }, 350)
    return () => window.clearTimeout(id)
  }, [searchDraft])   // eslint-disable-line react-hooks/exhaustive-deps

  const update = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.delete('page')
    setParams(next)
  }

  const status = params.get('status') ?? 'LIVE'
  const page = Number(params.get('page') ?? '1')
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight">Motorcycle auctions</h1>
        <p className="mt-1 text-sm text-ink-500">
          Every bike is inspected and graded before it reaches the block.
        </p>
      </div>

      <div className="card space-y-3 p-4">
        <div className="flex flex-wrap gap-2">
          {STATUSES.map(([value, label]) => (
            <button
              key={value}
              onClick={() => update('status', value)}
              className={status === value ? 'btn-primary !py-2' : 'btn-ghost !py-2'}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
          <input
            className="input"
            placeholder="Search make, model or registration…"
            value={searchDraft}
            onChange={(e) => setSearchDraft(e.target.value)}
            aria-label="Search auctions"
          />
          <select
            className="input sm:w-44"
            value={params.get('sort') ?? 'ending_soon'}
            onChange={(e) => update('sort', e.target.value)}
            aria-label="Sort by"
          >
            {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select
            className="input sm:w-40"
            value={params.get('city') ?? ''}
            onChange={(e) => update('city', e.target.value || null)}
            aria-label="Filter by city"
          >
            <option value="">All cities</option>
            {['Bengaluru', 'Pune', 'Mumbai', 'Delhi', 'Chennai', 'Hyderabad', 'Kolkata', 'Jaipur', 'Ahmedabad', 'Chandigarh']
              .map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>

      {loading && !data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : data && data.items.length === 0 ? (
        <Empty
          title="Nothing matches those filters"
          hint="Try widening your search or switching to upcoming auctions."
        />
      ) : (
        <>
          <p className="text-sm text-ink-500">{data?.total} auctions</p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {data?.items.map((auction) => (
              <AuctionCard key={auction.id} auction={auction} remaining={clock.remaining} />
            ))}
          </div>

          {totalPages > 1 && (
            <nav className="flex items-center justify-center gap-2 pt-4" aria-label="Pagination">
              <button className="btn-ghost" disabled={page <= 1} onClick={() => update('page', String(page - 1))}>
                Previous
              </button>
              <span className="text-sm text-ink-500">Page {page} of {totalPages}</span>
              <button className="btn-ghost" disabled={page >= totalPages} onClick={() => update('page', String(page + 1))}>
                Next
              </button>
            </nav>
          )}
        </>
      )}
    </div>
  )
}
