import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../lib/api'
import { money } from '../lib/format'
import { useAuth } from '../hooks/useAuth'
import { useServerClock } from '../hooks/useServerClock'
import type { AuctionSummary, Deposit, Page } from '../lib/types'
import { AuctionCard } from '../components/AuctionCard'
import { Empty, ErrorNote, Spinner, Stat } from '../components/ui'

type Tab = 'bids' | 'watchlist'

export function Account() {
  const { user } = useAuth()
  const clock = useServerClock()
  const [tab, setTab] = useState<Tab>('bids')
  const [deposit, setDeposit] = useState<Deposit | null>(null)
  const [list, setList] = useState<Page<AuctionSummary> | null>(null)
  const [amount, setAmount] = useState('10000')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadDeposit = useCallback(
    () => api.get<Deposit>('/api/v1/me/deposit').then(setDeposit).catch(() => {}),
    [],
  )
  const loadList = useCallback(
    () => api.get<Page<AuctionSummary>>(`/api/v1/me/${tab}`).then(setList).catch(() => {}),
    [tab],
  )

  useEffect(() => { void loadDeposit() }, [loadDeposit])
  useEffect(() => { setList(null); void loadList() }, [loadList])

  async function move(path: 'top-up' | 'withdraw') {
    setBusy(true); setError(null)
    try {
      setDeposit(await api.post<Deposit>(`/api/v1/me/deposit/${path}`, { amount }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  if (!user) return null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight">{user.full_name}</h1>
        <p className="mt-1 text-sm text-ink-500">
          {user.email} · {user.kyc_verified ? 'Verified — ready to bid' : 'Verification pending'}
        </p>
      </div>

      <section className="card p-5">
        <h2 className="font-semibold">Refundable deposit</h2>
        <p className="mt-1 text-sm text-ink-500">
          Held only while you are the leading bidder, and released the moment you are outbid.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Stat label="Balance" value={money(deposit?.balance ?? 0)} />
          <Stat label="Held" value={money(deposit?.held ?? 0)} tone="text-amber-500" />
          <Stat label="Available" value={money(deposit?.available ?? 0)} tone="text-emerald-500" />
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-2">
          <div className="min-w-40 flex-1">
            <label className="label" htmlFor="amount">Amount</label>
            <input id="amount" className="input tabular" inputMode="numeric" value={amount}
                   onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ''))} />
          </div>
          <button className="btn-primary" disabled={busy} onClick={() => move('top-up')}>
            {busy ? <Spinner className="h-4 w-4" /> : 'Add funds'}
          </button>
          <button className="btn-ghost" disabled={busy} onClick={() => move('withdraw')}>
            Withdraw
          </button>
        </div>
        {error && <div className="mt-3"><ErrorNote message={error} /></div>}
        <p className="mt-3 text-xs text-ink-500">
          Demo note: top-ups are simulated. A production build would credit this ledger from a
          signed payment-gateway webhook, never from a client request.
        </p>
      </section>

      <section className="space-y-4">
        <div className="flex gap-2">
          {(['bids', 'watchlist'] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={tab === t ? 'btn-primary !py-2' : 'btn-ghost !py-2'}>
              {t === 'bids' ? 'Auctions I bid on' : 'Watchlist'}
            </button>
          ))}
        </div>

        {list === null ? (
          <div className="grid place-items-center py-12"><Spinner className="h-6 w-6 text-brand-500" /></div>
        ) : list.items.length === 0 ? (
          <Empty
            title={tab === 'bids' ? 'You have not bid on anything yet' : 'Your watchlist is empty'}
            hint="Browse live auctions to get started."
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {list.items.map((a) => <AuctionCard key={a.id} auction={a} remaining={clock.remaining} />)}
          </div>
        )}
      </section>
    </div>
  )
}
