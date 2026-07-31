import clsx from 'clsx'
import { countdown } from '../lib/format'
import { useTicker } from '../hooks/useTicker'

/**
 * Deadline rendered against the *server's* clock.
 *
 * `remaining` is supplied by the caller's server-clock hook, so a browser with
 * a skewed system time still shows the auction's truth. Inside the final
 * minute the display turns urgent and the tick rate is already 1s.
 */
export function Countdown({
  endsAt,
  remaining,
  className,
  intervalMs = 1000,
  showLabel = true,
}: {
  endsAt: string
  remaining: (iso: string) => number
  className?: string
  intervalMs?: number
  showLabel?: boolean
}) {
  useTicker(intervalMs)
  const ms = remaining(endsAt)
  const urgent = ms > 0 && ms < 60_000
  const soon = ms > 0 && ms < 10 * 60_000

  return (
    <span
      className={clsx(
        'tabular font-semibold',
        urgent ? 'text-rose-500' : soon ? 'text-amber-500' : '',
        className,
      )}
      title={new Date(endsAt).toLocaleString('en-IN')}
    >
      {showLabel && ms > 0 && <span className="mr-1 font-normal text-ink-500">ends in</span>}
      {countdown(ms)}
    </span>
  )
}
