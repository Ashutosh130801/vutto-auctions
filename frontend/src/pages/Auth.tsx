import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { ApiError } from '../lib/api'
import { useAuth } from '../hooks/useAuth'
import { ErrorNote, Spinner } from '../components/ui'

function Shell({ title, subtitle, children, footer }: {
  title: string; subtitle: string; children: React.ReactNode; footer: React.ReactNode
}) {
  return (
    <div className="mx-auto max-w-md py-8">
      <div className="card p-7">
        <h1 className="text-xl font-bold">{title}</h1>
        <p className="mt-1 text-sm text-ink-500">{subtitle}</p>
        <div className="mt-6 space-y-4">{children}</div>
      </div>
      <p className="mt-4 text-center text-sm text-ink-500">{footer}</p>
    </div>
  )
}

export function Login() {
  const { login, user } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/auctions" replace />

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true); setError(null)
    try {
      await login(email, password)
      navigate('/auctions')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not sign in. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell
      title="Welcome back"
      subtitle="Sign in to bid, watch auctions and manage your deposit."
      footer={<>New here? <Link to="/register" className="font-semibold text-brand-500 hover:underline">Create an account</Link></>}
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="email" required className="input"
                 value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" required className="input"
                 value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && <ErrorNote message={error} />}
        <button className="btn-primary w-full py-3" disabled={busy}>
          {busy ? <><Spinner className="h-4 w-4" /> Signing in…</> : 'Sign in'}
        </button>
      </form>

      <div className="rounded-xl bg-ink-100 px-3.5 py-3 text-xs text-ink-500 dark:bg-ink-800">
        <p className="font-semibold">Demo accounts (after seeding)</p>
        <p className="mt-1">Buyer: <code>aarav@vutto.example.com</code> / <code>Demo@12345</code></p>
        <p>Admin: <code>admin@vutto.example.com</code> / <code>Admin@12345</code></p>
      </div>
    </Shell>
  )
}

export function Register() {
  const { register, user } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ full_name: '', email: '', password: '', phone: '' })
  const [error, setError] = useState<ApiError | string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/auctions" replace />

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true); setError(null)
    try {
      await register({ ...form, phone: form.phone || undefined })
      navigate('/auctions')
    } catch (err) {
      setError(err instanceof ApiError ? err : 'Could not create the account.')
    } finally {
      setBusy(false)
    }
  }

  const fieldErrors: { field: string; message: string }[] =
    error instanceof ApiError ? (error.details.fields ?? []) : []

  return (
    <Shell
      title="Create your account"
      subtitle="Takes a minute. You will need a refundable deposit before your first bid."
      footer={<>Already registered? <Link to="/login" className="font-semibold text-brand-500 hover:underline">Sign in</Link></>}
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="full_name">Full name</label>
          <input id="full_name" required className="input" value={form.full_name} onChange={set('full_name')} />
        </div>
        <div>
          <label className="label" htmlFor="reg-email">Email</label>
          <input id="reg-email" type="email" autoComplete="email" required className="input"
                 value={form.email} onChange={set('email')} />
        </div>
        <div>
          <label className="label" htmlFor="phone">Phone <span className="normal-case text-ink-400">(optional)</span></label>
          <input id="phone" className="input" value={form.phone} onChange={set('phone')} />
        </div>
        <div>
          <label className="label" htmlFor="reg-password">Password</label>
          <input id="reg-password" type="password" autoComplete="new-password" required className="input"
                 value={form.password} onChange={set('password')} />
          <p className="mt-1.5 text-xs text-ink-500">
            At least 10 characters, with upper case, lower case and a digit.
          </p>
        </div>

        {fieldErrors.length > 0 ? (
          <ErrorNote message={fieldErrors.map((f) => `${f.field}: ${f.message}`).join(' · ')} />
        ) : error ? (
          <ErrorNote message={error instanceof ApiError ? error.message : error} />
        ) : null}

        <button className="btn-primary w-full py-3" disabled={busy}>
          {busy ? <><Spinner className="h-4 w-4" /> Creating…</> : 'Create account'}
        </button>
      </form>
    </Shell>
  )
}
