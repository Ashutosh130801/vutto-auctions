import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { useAuth } from '../hooks/useAuth'
import { api } from '../lib/api'
import { money } from '../lib/format'
import type { Deposit, Notification } from '../lib/types'

function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('vutto.theme', dark ? 'dark' : 'light')
  }, [dark])
  return (
    <button
      onClick={() => setDark((d) => !d)}
      className="btn-ghost !px-2.5"
      aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
    >
      <span aria-hidden>{dark ? '☀' : '☾'}</span>
    </button>
  )
}

function Bell() {
  const [items, setItems] = useState<Notification[]>([])
  const [open, setOpen] = useState(false)

  const load = () => api.get<Notification[]>('/api/v1/me/notifications?limit=15').then(setItems).catch(() => {})
  useEffect(() => {
    void load()
    const id = window.setInterval(load, 30_000)
    return () => window.clearInterval(id)
  }, [])

  const unread = items.filter((n) => !n.read_at).length
  return (
    <div className="relative">
      <button
        className="btn-ghost relative !px-2.5"
        onClick={() => {
          setOpen((o) => !o)
          if (!open && unread) api.post('/api/v1/me/notifications/read').then(load).catch(() => {})
        }}
        aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
      >
        <span aria-hidden>🔔</span>
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-brand-500 px-1 text-[10px] font-bold text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="card absolute right-0 z-30 mt-2 max-h-96 w-80 overflow-y-auto p-1.5">
          {items.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-ink-500">Nothing yet.</p>
          ) : (
            items.map((n) => (
              <Link
                key={n.id}
                to={n.data.slug ? `/auctions/${n.data.slug}` : '/auctions'}
                onClick={() => setOpen(false)}
                className="block rounded-lg px-3 py-2.5 hover:bg-ink-100 dark:hover:bg-ink-800"
              >
                <p className="text-sm font-semibold">{n.title}</p>
                <p className="mt-0.5 text-xs text-ink-500">{n.body}</p>
              </Link>
            ))
          )}
        </div>
      )}
    </div>
  )
}

function DepositPill() {
  const [deposit, setDeposit] = useState<Deposit | null>(null)
  useEffect(() => {
    const load = () => api.get<Deposit>('/api/v1/me/deposit').then(setDeposit).catch(() => {})
    void load()
    const id = window.setInterval(load, 20_000)
    return () => window.clearInterval(id)
  }, [])
  if (!deposit) return null
  return (
    <Link
      to="/account"
      className="hidden rounded-xl border border-ink-200 px-3 py-1.5 text-xs sm:block dark:border-ink-700"
      title={`${money(deposit.held)} held against leading bids`}
    >
      <span className="text-ink-500">Deposit </span>
      <span className="font-semibold tabular">{money(deposit.available)}</span>
    </Link>
  )
}

export function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const navClass = ({ isActive }: { isActive: boolean }) =>
    clsx(
      'rounded-lg px-3 py-2 text-sm font-medium transition-colors',
      isActive ? 'bg-ink-100 dark:bg-ink-800' : 'text-ink-500 hover:text-ink-900 dark:hover:text-ink-100',
    )

  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-3 focus:rounded-lg focus:bg-brand-500 focus:px-4 focus:py-2 focus:text-white">
        Skip to content
      </a>

      <header className="sticky top-0 z-20 border-b border-ink-200 bg-white/85 backdrop-blur dark:border-ink-800 dark:bg-ink-950/85">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-extrabold tracking-tight">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-500 text-white">V</span>
            <span className="hidden sm:block">Vutto<span className="text-brand-500">Auctions</span></span>
          </Link>

          <nav className="ml-2 hidden items-center gap-1 md:flex">
            <NavLink to="/auctions" className={navClass}>Browse</NavLink>
            {user && <NavLink to="/account" className={navClass}>My bids</NavLink>}
            {user?.role === 'ADMIN' && <NavLink to="/admin" className={navClass}>Admin</NavLink>}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            {user ? (
              <>
                <DepositPill />
                <Bell />
                <button
                  className="btn-ghost"
                  onClick={async () => { await logout(); navigate('/') }}
                >
                  Sign out
                </button>
              </>
            ) : (
              <>
                <Link to="/login" className="btn-ghost">Sign in</Link>
                <Link to="/register" className="btn-primary">Get started</Link>
              </>
            )}
          </div>
        </div>

        <nav className="flex gap-1 overflow-x-auto border-t border-ink-200 px-4 py-2 md:hidden dark:border-ink-800">
          <NavLink to="/auctions" className={navClass}>Browse</NavLink>
          {user && <NavLink to="/account" className={navClass}>My bids</NavLink>}
          {user?.role === 'ADMIN' && <NavLink to="/admin" className={navClass}>Admin</NavLink>}
        </nav>
      </header>

      <main id="main" className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>

      <footer className="mx-auto max-w-7xl px-4 py-10 text-xs text-ink-500">
        <p>
          Proxy bidding: you enter a maximum, we bid the minimum needed to keep you in front.
          Auctions extend automatically if a bid lands in the final moments.
        </p>
      </footer>
    </div>
  )
}
