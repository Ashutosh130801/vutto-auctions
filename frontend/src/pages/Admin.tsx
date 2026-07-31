import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../lib/api'
import { compactMoney, datetime, money, number } from '../lib/format'
import type { AdminStats, AuctionSummary, Bike, Page } from '../lib/types'
import { ErrorNote, Spinner, Stat, StatusChip } from '../components/ui'

interface AuditEntry {
  id: string
  actor_email: string | null
  action: string
  entity_type: string
  entity_id: string | null
  ip_address: string | null
  created_at: string
}

const emptyBike = {
  registration_number: '', make: '', model: '', variant: '', year: 2022,
  engine_cc: 350, odometer_km: 15000, city: 'Bengaluru', condition_grade: 'A',
  inspection_score: 90, estimated_value: '150000', description: '',
}

export function Admin() {
  const [tab, setTab] = useState<'overview' | 'inventory' | 'schedule' | 'audit'>('overview')
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [bikes, setBikes] = useState<Page<Bike> | null>(null)
  const [auctions, setAuctions] = useState<Page<AuctionSummary> | null>(null)
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [s, b, a, l] = await Promise.all([
        api.get<AdminStats>('/api/v1/admin/stats'),
        api.get<Page<Bike>>('/api/v1/admin/bikes?page_size=50'),
        api.get<Page<AuctionSummary>>('/api/v1/auctions?page_size=50&sort=newest'),
        api.get<AuditEntry[]>('/api/v1/admin/audit?limit=60'),
      ])
      setStats(s); setBikes(b); setAuctions(a); setAudit(l)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load admin data.')
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-extrabold tracking-tight">Operations</h1>
        <button className="btn-ghost" onClick={() => void refresh()}>Refresh</button>
      </div>

      {error && <ErrorNote message={error} />}
      {notice && (
        <p className="rounded-xl bg-emerald-500/10 px-4 py-3 text-sm text-emerald-600 dark:text-emerald-400">
          {notice}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {(['overview', 'inventory', 'schedule', 'audit'] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={tab === t ? 'btn-primary !py-2 capitalize' : 'btn-ghost !py-2 capitalize'}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Live auctions" value={number(stats?.live_auctions ?? 0)} tone="text-emerald-500" />
          <Stat label="Closing within the hour" value={number(stats?.ending_within_hour ?? 0)} tone="text-amber-500" />
          <Stat label="Scheduled" value={number(stats?.scheduled_auctions ?? 0)} />
          <Stat label="Ended" value={number(stats?.ended_auctions ?? 0)} />
          <Stat label="Total bids" value={number(stats?.total_bids ?? 0)} />
          <Stat label="Registered users" value={number(stats?.total_users ?? 0)} />
          <Stat label="GMV (sold)" value={compactMoney(stats?.gross_merchandise_value ?? 0)} tone="text-brand-500" />
        </div>
      )}

      {tab === 'inventory' && <Inventory bikes={bikes} onDone={(m) => { setNotice(m); void refresh() }} onError={setError} />}

      {tab === 'schedule' && (
        <ScheduleTab
          bikes={bikes}
          auctions={auctions}
          onDone={(m) => { setNotice(m); void refresh() }}
          onError={setError}
        />
      )}

      {tab === 'audit' && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-ink-200 text-left text-xs uppercase tracking-wide text-ink-500 dark:border-ink-800">
              <tr>
                <th className="px-4 py-3">When</th><th className="px-4 py-3">Actor</th>
                <th className="px-4 py-3">Action</th><th className="px-4 py-3">Entity</th>
                <th className="px-4 py-3">IP</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-200 dark:divide-ink-800">
              {audit.map((entry) => (
                <tr key={entry.id}>
                  <td className="whitespace-nowrap px-4 py-2.5 text-ink-500">{datetime(entry.created_at)}</td>
                  <td className="px-4 py-2.5">{entry.actor_email ?? '—'}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{entry.action}</td>
                  <td className="px-4 py-2.5 text-ink-500">{entry.entity_type}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-ink-500">{entry.ip_address ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {audit.length === 0 && <p className="p-8 text-center text-sm text-ink-500">No activity recorded yet.</p>}
        </div>
      )}
    </div>
  )
}

function Inventory({ bikes, onDone, onError }: {
  bikes: Page<Bike> | null
  onDone: (m: string) => void
  onError: (m: string) => void
}) {
  const [form, setForm] = useState(emptyBike)
  const [busy, setBusy] = useState(false)

  const set = (key: keyof typeof emptyBike) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      await api.post('/api/v1/admin/bikes', {
        ...form,
        year: Number(form.year),
        engine_cc: Number(form.engine_cc),
        odometer_km: Number(form.odometer_km),
        inspection_score: Number(form.inspection_score),
        variant: form.variant || null,
        description: form.description || null,
        images: [`https://picsum.photos/seed/${form.make}-${form.model}/1200/800`],
      })
      setForm(emptyBike)
      onDone('Bike added to inventory.')
    } catch (err) {
      onError(err instanceof ApiError ? err.message : 'Could not add the bike.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
      <form onSubmit={submit} className="card space-y-3 p-5">
        <h2 className="font-semibold">Add a bike</h2>
        {([
          ['registration_number', 'Registration', 'text'],
          ['make', 'Make', 'text'], ['model', 'Model', 'text'], ['variant', 'Variant', 'text'],
          ['year', 'Year', 'number'], ['engine_cc', 'Engine (cc)', 'number'],
          ['odometer_km', 'Odometer (km)', 'number'], ['city', 'City', 'text'],
          ['inspection_score', 'Inspection score', 'number'],
          ['estimated_value', 'Market estimate', 'text'],
        ] as const).map(([key, label, type]) => (
          <div key={key}>
            <label className="label" htmlFor={key}>{label}</label>
            <input id={key} type={type} className="input" required={key !== 'variant'}
                   value={String(form[key])} onChange={set(key)} />
          </div>
        ))}
        <div>
          <label className="label" htmlFor="condition_grade">Condition grade</label>
          <select id="condition_grade" className="input" value={form.condition_grade} onChange={set('condition_grade')}>
            {['A', 'B', 'C', 'D'].map((g) => <option key={g}>{g}</option>)}
          </select>
        </div>
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? <Spinner className="h-4 w-4" /> : 'Add to inventory'}
        </button>
      </form>

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-ink-200 text-left text-xs uppercase tracking-wide text-ink-500 dark:border-ink-800">
            <tr>
              <th className="px-4 py-3">Bike</th><th className="px-4 py-3">Reg.</th>
              <th className="px-4 py-3">Grade</th><th className="px-4 py-3">Estimate</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-200 dark:divide-ink-800">
            {bikes?.items.map((bike) => (
              <tr key={bike.id}>
                <td className="px-4 py-2.5 font-medium">{bike.year} {bike.make} {bike.model}</td>
                <td className="px-4 py-2.5 font-mono text-xs">{bike.registration_number}</td>
                <td className="px-4 py-2.5">{bike.condition_grade} · {bike.inspection_score}</td>
                <td className="px-4 py-2.5 tabular">{money(bike.estimated_value)}</td>
                <td className="px-4 py-2.5 text-xs text-ink-500">{bike.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ScheduleTab({ bikes, auctions, onDone, onError }: {
  bikes: Page<Bike> | null
  auctions: Page<AuctionSummary> | null
  onDone: (m: string) => void
  onError: (m: string) => void
}) {
  const available = bikes?.items.filter((b) => b.status === 'READY' || b.status === 'DRAFT') ?? []
  const [form, setForm] = useState({
    bike_id: '', start_price: '100000', bid_increment: '1000',
    reserve_price: '', deposit_required: '5000', hours: '24', starts_in_minutes: '2',
  })
  const [busy, setBusy] = useState(false)

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      const starts = new Date(Date.now() + Number(form.starts_in_minutes) * 60_000)
      await api.post('/api/v1/admin/auctions', {
        bike_id: form.bike_id,
        starts_at: starts.toISOString(),
        ends_at: new Date(starts.getTime() + Number(form.hours) * 3_600_000).toISOString(),
        start_price: form.start_price,
        bid_increment: form.bid_increment,
        reserve_price: form.reserve_price || null,
        deposit_required: form.deposit_required,
      })
      onDone('Auction scheduled.')
    } catch (err) {
      onError(err instanceof ApiError ? err.message : 'Could not schedule the auction.')
    } finally {
      setBusy(false)
    }
  }

  async function act(id: string, action: 'close' | 'cancel') {
    try {
      if (action === 'cancel') {
        const reason = window.prompt('Reason for cancelling this auction?')
        if (!reason) return
        await api.post(`/api/v1/admin/auctions/${id}/cancel`, { reason })
      } else {
        await api.post(`/api/v1/admin/auctions/${id}/close`)
      }
      onDone(action === 'cancel' ? 'Auction cancelled; deposits released.' : 'Auction closed.')
    } catch (err) {
      onError(err instanceof ApiError ? err.message : 'Action failed.')
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
      <form onSubmit={submit} className="card space-y-3 p-5">
        <h2 className="font-semibold">Schedule an auction</h2>
        <div>
          <label className="label" htmlFor="bike_id">Bike</label>
          <select id="bike_id" className="input" required value={form.bike_id} onChange={set('bike_id')}>
            <option value="">Select a ready bike…</option>
            {available.map((b) => (
              <option key={b.id} value={b.id}>{b.year} {b.make} {b.model} — {b.registration_number}</option>
            ))}
          </select>
          {available.length === 0 && (
            <p className="mt-1.5 text-xs text-amber-500">
              No bikes are free. Add one in Inventory, or wait for a current auction to end.
            </p>
          )}
        </div>
        {([
          ['start_price', 'Start price'], ['bid_increment', 'Bid increment'],
          ['reserve_price', 'Reserve (optional)'], ['deposit_required', 'Deposit required'],
          ['starts_in_minutes', 'Starts in (minutes)'], ['hours', 'Runs for (hours)'],
        ] as const).map(([key, label]) => (
          <div key={key}>
            <label className="label" htmlFor={key}>{label}</label>
            <input id={key} className="input tabular" value={form[key]} onChange={set(key)} />
          </div>
        ))}
        <button className="btn-primary w-full" disabled={busy || !form.bike_id}>
          {busy ? <Spinner className="h-4 w-4" /> : 'Schedule'}
        </button>
      </form>

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-ink-200 text-left text-xs uppercase tracking-wide text-ink-500 dark:border-ink-800">
            <tr>
              <th className="px-4 py-3">Auction</th><th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Price</th><th className="px-4 py-3">Bids</th>
              <th className="px-4 py-3">Ends</th><th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-200 dark:divide-ink-800">
            {auctions?.items.map((a) => (
              <tr key={a.id}>
                <td className="px-4 py-2.5 font-medium">{a.title}</td>
                <td className="px-4 py-2.5"><StatusChip status={a.status} outcome={a.outcome} /></td>
                <td className="px-4 py-2.5 tabular">{money(a.current_price)}</td>
                <td className="px-4 py-2.5 tabular">{a.bid_count}</td>
                <td className="whitespace-nowrap px-4 py-2.5 text-xs text-ink-500">{datetime(a.ends_at)}</td>
                <td className="whitespace-nowrap px-4 py-2.5 text-right">
                  {(a.status === 'LIVE' || a.status === 'SCHEDULED') && (
                    <>
                      <button className="text-xs font-semibold text-brand-500 hover:underline" onClick={() => act(a.id, 'close')}>Close</button>
                      <button className="ml-3 text-xs font-semibold text-rose-500 hover:underline" onClick={() => act(a.id, 'cancel')}>Cancel</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
