/**
 * API client.
 *
 * Two things here are worth more than they look:
 *
 * 1. **Typed errors.** The backend returns one envelope with a stable machine
 *    `code`; we surface that as `ApiError.code` so UI branches on codes, never
 *    on message text.
 * 2. **Single-flight refresh.** When an access token expires, N in-flight
 *    requests would each try to refresh, and because refresh tokens rotate,
 *    N-1 of them would present a spent token and get the whole family revoked —
 *    logging the user out for being *too active*. We collapse concurrent
 *    refreshes into one shared promise.
 */
import type { TokenResponse } from './types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''
const ACCESS_KEY = 'vutto.access'
const REFRESH_KEY = 'vutto.refresh'

export class ApiError extends Error {
  code: string
  status: number
  details: Record<string, any>
  requestId?: string
  constructor(status: number, code: string, message: string, details = {}, requestId?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.requestId = requestId
  }
}

export const tokens = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  set(pair: { access_token: string; refresh_token: string }) {
    localStorage.setItem(ACCESS_KEY, pair.access_token)
    localStorage.setItem(REFRESH_KEY, pair.refresh_token)
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

let refreshInFlight: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight
  const stored = tokens.refresh()
  if (!stored) return null

  refreshInFlight = (async () => {
    try {
      const response = await fetch(`${BASE}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: stored }),
      })
      if (!response.ok) {
        tokens.clear()
        return null
      }
      const pair = (await response.json()) as TokenResponse
      tokens.set(pair)
      return pair.access_token
    } catch {
      return null
    } finally {
      // Release on the next tick so late awaiters still see this result.
      setTimeout(() => { refreshInFlight = null }, 0)
    }
  })()
  return refreshInFlight
}

interface RequestOptions {
  method?: string
  body?: unknown
  auth?: boolean
  idempotencyKey?: string
  signal?: AbortSignal
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true, idempotencyKey, signal } = options

  const send = async (token: string | null): Promise<Response> => {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (auth && token) headers.Authorization = `Bearer ${token}`
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey
    return fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  }

  let response = await send(auth ? tokens.access() : null)

  if (response.status === 401 && auth && tokens.refresh()) {
    const fresh = await refreshAccessToken()
    if (fresh) response = await send(fresh)
  }

  if (response.status === 204) return undefined as T
  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    const err = payload?.error ?? {}
    throw new ApiError(
      response.status,
      err.code ?? 'UNKNOWN',
      err.message ?? response.statusText,
      err.details ?? {},
      payload?.request_id,
    )
  }
  return payload as T
}

export const api = {
  get: <T,>(path: string, opts?: RequestOptions) => request<T>(path, { ...opts, method: 'GET' }),
  post: <T,>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'POST', body }),
  put: <T,>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  patch: <T,>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  del: <T,>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export const wsUrl = (auctionId: string): string => {
  const base = BASE || window.location.origin
  const url = new URL(`/api/v1/auctions/${auctionId}/stream`, base)
  url.protocol = url.protocol.replace('http', 'ws')
  const token = tokens.access()
  if (token) url.searchParams.set('token', token)
  return url.toString()
}
