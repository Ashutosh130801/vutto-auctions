import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './hooks/useAuth'
import { Layout } from './components/Layout'
import { Spinner } from './components/ui'
import { Home } from './pages/Home'
import { Browse } from './pages/Browse'
import { AuctionRoom } from './pages/AuctionRoom'
import { Login, Register } from './pages/Auth'
import { Account } from './pages/Account'
import { Admin } from './pages/Admin'
import './index.css'

// Honour the saved theme before first paint so there is no flash of the wrong one.
if (localStorage.getItem('vutto.theme') === 'light') {
  document.documentElement.classList.remove('dark')
}

function Guard({ children, adminOnly = false }: { children: JSX.Element; adminOnly?: boolean }) {
  const { user, loading } = useAuth()
  if (loading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8 text-brand-500" /></div>
  if (!user) return <Navigate to="/login" replace />
  if (adminOnly && user.role !== 'ADMIN') return <Navigate to="/auctions" replace />
  return children
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="auctions" element={<Browse />} />
            <Route path="auctions/:slug" element={<AuctionRoom />} />
            <Route path="login" element={<Login />} />
            <Route path="register" element={<Register />} />
            <Route path="account" element={<Guard><Account /></Guard>} />
            <Route path="admin" element={<Guard adminOnly><Admin /></Guard>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
