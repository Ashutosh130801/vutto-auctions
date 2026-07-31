import { useEffect, useState } from 'react'

/**
 * Re-renders on an interval.
 *
 * Countdowns need a repaint even when no data changed. Anything that is not
 * about to close ticks once a second at most; call sites pass a slower interval
 * for list views so a page of 20 cards is not doing 20 renders a second.
 */
export function useTicker(intervalMs = 1000): number {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return tick
}
