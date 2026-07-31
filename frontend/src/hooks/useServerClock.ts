import { useCallback, useRef, useState } from 'react'

/**
 * Tracks the offset between this browser's clock and the server's.
 *
 * Auction countdowns are the one place where a wrong clock is not cosmetic: a
 * user whose laptop is 40 seconds fast would think the auction is over while
 * bidding is still open (or vice versa). Every realtime frame carries
 * `server_time`, so we continuously re-derive `offset = server - client` and
 * render every deadline through it.
 *
 * A simple exponential moving average smooths out per-frame network jitter
 * without lagging behind a genuine clock correction.
 */
export function useServerClock() {
  const offsetRef = useRef(0)
  const [, force] = useState(0)

  const sync = useCallback((serverTimeIso: string) => {
    const sample = new Date(serverTimeIso).getTime() - Date.now()
    if (!Number.isFinite(sample)) return
    offsetRef.current =
      offsetRef.current === 0 ? sample : offsetRef.current * 0.8 + sample * 0.2
  }, [])

  const now = useCallback(() => Date.now() + offsetRef.current, [])

  const remaining = useCallback(
    (deadlineIso: string) => new Date(deadlineIso).getTime() - (Date.now() + offsetRef.current),
    [],
  )

  return { sync, now, remaining, offset: offsetRef.current, force: () => force((n) => n + 1) }
}
