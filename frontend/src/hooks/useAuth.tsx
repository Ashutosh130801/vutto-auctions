import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, tokens } from '../lib/api'
import type { TokenResponse, User } from '../lib/types'

interface AuthValue {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (input: { email: string; password: string; full_name: string; phone?: string }) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refreshUser = useCallback(async () => {
    if (!tokens.access()) { setUser(null); return }
    try {
      setUser(await api.get<User>('/api/v1/me'))
    } catch {
      tokens.clear()
      setUser(null)
    }
  }, [])

  useEffect(() => { void refreshUser().finally(() => setLoading(false)) }, [refreshUser])

  const adopt = (pair: TokenResponse) => { tokens.set(pair); setUser(pair.user) }

  const value = useMemo<AuthValue>(() => ({
    user,
    loading,
    login: async (email, password) => {
      adopt(await api.post<TokenResponse>('/api/v1/auth/login', { email, password }, { auth: false }))
    },
    register: async (input) => {
      adopt(await api.post<TokenResponse>('/api/v1/auth/register', input, { auth: false }))
    },
    logout: async () => {
      const refresh = tokens.refresh()
      // Best effort: a failed revoke must not trap the user in a signed-in UI.
      if (refresh) await api.post('/api/v1/auth/logout', { refresh_token: refresh }).catch(() => {})
      tokens.clear()
      setUser(null)
    },
    refreshUser,
  }), [user, loading, refreshUser])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
