/** Presentation-only helpers. No business rules live here. */

const INR = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})

export const money = (value: string | number | null | undefined): string =>
  value === null || value === undefined ? '—' : INR.format(Number(value))

export const compactMoney = (value: string | number): string => {
  const n = Number(value)
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`
  return INR.format(n)
}

export const number = (value: number): string => new Intl.NumberFormat('en-IN').format(value)

/**
 * Human countdown. Deliberately drops to seconds only inside the last hour so
 * the list view does not re-render every card every second.
 */
export function countdown(msRemaining: number): string {
  if (msRemaining <= 0) return 'Ended'
  const s = Math.floor(msRemaining / 1000)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

export const relative = (iso: string): string => {
  const diff = Date.now() - new Date(iso).getTime()
  const s = Math.floor(diff / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

export const datetime = (iso: string): string =>
  new Date(iso).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
  })
