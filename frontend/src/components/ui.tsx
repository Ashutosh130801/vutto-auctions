import clsx from 'clsx'
import type { ReactNode } from 'react'
import type { AuctionStatus } from '../lib/types'

export function StatusChip({ status, outcome }: { status: AuctionStatus; outcome?: string }) {
  const map: Record<string, { label: string; className: string; dot?: boolean }> = {
    LIVE: { label: 'Live', className: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400', dot: true },
    SCHEDULED: { label: 'Upcoming', className: 'bg-sky-500/15 text-sky-600 dark:text-sky-400' },
    ENDED: { label: 'Ended', className: 'bg-ink-500/15 text-ink-600 dark:text-ink-300' },
    SETTLED: { label: 'Settled', className: 'bg-ink-500/15 text-ink-600 dark:text-ink-300' },
    CANCELLED: { label: 'Cancelled', className: 'bg-rose-500/15 text-rose-600 dark:text-rose-400' },
  }
  const meta = map[status] ?? map.ENDED!
  const label = status === 'ENDED' && outcome === 'SOLD' ? 'Sold' : meta.label
  return (
    <span className={clsx('chip', meta.className)}>
      {meta.dot && <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />}
      {label}
    </span>
  )
}

export function GradeBadge({ grade, score }: { grade: string; score: number }) {
  const tone =
    grade === 'A' ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
    : grade === 'B' ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400'
    : grade === 'C' ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
    : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
  return (
    <span className={clsx('chip', tone)} title={`Inspection score ${score}/100`}>
      Grade {grade} · {score}
    </span>
  )
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={clsx('animate-spin', className)} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-16 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-full bg-ink-100 text-2xl dark:bg-ink-800">
        <span aria-hidden>◎</span>
      </div>
      <p className="text-base font-semibold">{title}</p>
      {hint && <p className="max-w-sm text-sm text-ink-500">{hint}</p>}
      {action}
    </div>
  )
}

export function ErrorNote({ message, requestId }: { message: string; requestId?: string }) {
  return (
    <div role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3.5 py-3 text-sm text-rose-600 dark:text-rose-300">
      <p className="font-medium">{message}</p>
      {requestId && <p className="mt-1 font-mono text-[11px] opacity-60">ref {requestId}</p>}
    </div>
  )
}

export function SkeletonCard() {
  return (
    <div className="card overflow-hidden">
      <div className="skeleton aspect-[4/3] rounded-none" />
      <div className="space-y-3 p-4">
        <div className="skeleton h-4 w-3/4" />
        <div className="skeleton h-3 w-1/2" />
        <div className="skeleton h-8 w-full" />
      </div>
    </div>
  )
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div className="card px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-500">{label}</p>
      <p className={clsx('mt-1 text-xl font-bold tabular', tone)}>{value}</p>
    </div>
  )
}
