import { useCallback, useEffect, useRef, useState } from 'react'
import { wsUrl } from '../lib/api'
import type { Bid, RealtimeFrame } from '../lib/types'

export interface LiveState {
  currentPrice: string
  minimumNextBid: string
  bidCount: number
  bidderCount: number
  endsAt: string
  leadingUserId: string | null
  reserveMet: boolean
  version: number
  status: string
}

type Status = 'connecting' | 'open' | 'reconnecting' | 'closed'

interface Options {
  auctionId: string
  onBids?: (bids: Bid[]) => void
  onExtended?: (endsAt: string) => void
  onEnded?: (payload: Record<string, any>) => void
  onOutbid?: (payload: Record<string, any>) => void
  onServerTime?: (iso: string) => void
}

const MAX_BACKOFF_MS = 15_000

/**
 * Live auction socket.
 *
 * Three details that matter:
 *
 * - **Version-guarded state.** Frames are at-least-once and may arrive out of
 *   order. We drop any frame whose `version` is not newer than what is already
 *   rendered, so a delayed duplicate can never make the price appear to fall.
 * - **Exponential backoff with jitter.** When the API restarts, every connected
 *   browser tries to reconnect at once. Jitter spreads the thundering herd.
 * - **Reconnect implies resync.** The socket is an accelerator, not the source
 *   of truth, so on reconnect the caller refetches over HTTP; anything missed
 *   while disconnected is recovered.
 */
export function useAuctionStream({
  auctionId, onBids, onExtended, onEnded, onOutbid, onServerTime,
}: Options) {
  const [status, setStatus] = useState<Status>('connecting')
  const [live, setLive] = useState<LiveState | null>(null)
  const [viewers, setViewers] = useState(0)
  const [reconnectCount, setReconnectCount] = useState(0)

  const socketRef = useRef<WebSocket | null>(null)
  const attemptRef = useRef(0)
  const timerRef = useRef<number>()
  const versionRef = useRef(-1)
  const closedByUs = useRef(false)

  // Keep callbacks in a ref so changing them never tears down the socket.
  const handlers = useRef({ onBids, onExtended, onEnded, onOutbid, onServerTime })
  handlers.current = { onBids, onExtended, onEnded, onOutbid, onServerTime }

  const applyAuction = useCallback((auction: Record<string, any>) => {
    const version = Number(auction.version ?? -1)
    if (version <= versionRef.current) return   // stale or duplicate frame
    versionRef.current = version
    setLive({
      currentPrice: String(auction.current_price),
      minimumNextBid: String(auction.minimum_next_bid),
      bidCount: Number(auction.bid_count ?? 0),
      bidderCount: Number(auction.bidder_count ?? 0),
      endsAt: String(auction.ends_at),
      leadingUserId: auction.leading_user_id ?? null,
      reserveMet: Boolean(auction.reserve_met ?? true),
      version,
      status: String(auction.status ?? 'LIVE'),
    })
  }, [])

  const connect = useCallback(() => {
    closedByUs.current = false
    const socket = new WebSocket(wsUrl(auctionId))
    socketRef.current = socket

    socket.onopen = () => {
      attemptRef.current = 0
      setStatus('open')
    }

    socket.onmessage = (event) => {
      let frame: RealtimeFrame
      try { frame = JSON.parse(event.data) } catch { return }
      handlers.current.onServerTime?.(frame.server_time)

      switch (frame.event) {
        case 'snapshot':
          if (frame.data.auction) applyAuction(frame.data.auction)
          setViewers(Number(frame.data.viewers ?? 0))
          break
        case 'auction.bid_placed':
          if (frame.data.auction) applyAuction(frame.data.auction)
          if (Array.isArray(frame.data.bids)) handlers.current.onBids?.(frame.data.bids as Bid[])
          break
        case 'auction.extended':
          if (frame.data.auction) applyAuction(frame.data.auction)
          handlers.current.onExtended?.(String(frame.data.ends_at))
          break
        case 'auction.ended':
          if (frame.data.auction) applyAuction(frame.data.auction)
          handlers.current.onEnded?.(frame.data)
          break
        case 'user.outbid':
          handlers.current.onOutbid?.(frame.data)
          break
        case 'presence':
        case 'heartbeat':
          if (frame.data.viewers !== undefined) setViewers(Number(frame.data.viewers))
          break
      }
    }

    socket.onclose = () => {
      if (closedByUs.current) { setStatus('closed'); return }
      setStatus('reconnecting')
      const attempt = Math.min(attemptRef.current++, 6)
      const backoff = Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempt)
      const jitter = Math.random() * backoff * 0.4   // spread the herd
      timerRef.current = window.setTimeout(() => {
        setReconnectCount((n) => n + 1)   // signals the caller to refetch
        connect()
      }, backoff + jitter)
    }

    socket.onerror = () => socket.close()
  }, [auctionId, applyAuction])

  useEffect(() => {
    versionRef.current = -1
    connect()
    return () => {
      closedByUs.current = true
      window.clearTimeout(timerRef.current)
      socketRef.current?.close()
    }
  }, [connect])

  /** Fold a locally-known state (e.g. our own bid response) into the stream. */
  const merge = useCallback((partial: Partial<LiveState> & { version: number }) => {
    if (partial.version <= versionRef.current) return
    versionRef.current = partial.version
    setLive((prev) => (prev ? { ...prev, ...partial } : prev))
  }, [])

  return { status, live, viewers, reconnectCount, merge }
}
