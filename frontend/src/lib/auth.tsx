import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { setAuthToken } from './api'
import { getMe, login as apiLogin, register as apiRegister } from './client'
import type { AuthUser } from './types'

/* ---------------------------------------------------------------------------
   AuthContext — giữ trạng thái đăng nhập (user + token) cho toàn app.

   - login/register: gọi client.ts facade (mock hoặc API thật), lưu token vào
     localStorage + đồng bộ vào api.ts (setAuthToken cho Bearer header).
   - Khởi tạo: đọc token từ localStorage → gọi getMe để khôi phục user.
   - ProtectedRoute: chặn route cần đăng nhập; AdminRoute: chỉ admin.
--------------------------------------------------------------------------- */

const TOKEN_KEY = 'p118_access_token'

interface AuthContextValue {
  user: AuthUser | null
  token: string | null
  initializing: boolean
  isAdmin: boolean
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string, email?: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [initializing, setInitializing] = useState(true)

  // Khôi phục phiên từ localStorage khi app load.
  useEffect(() => {
    const stored = localStorage.getItem(TOKEN_KEY)
    if (!stored) {
      setInitializing(false)
      return
    }
    setToken(stored)
    setAuthToken(stored)
    getMe(stored)
      .then((u) => setUser(u))
      .catch(() => {
        // Token cũ/hết hạn → xoá để user đăng nhập lại.
        localStorage.removeItem(TOKEN_KEY)
        setToken(null)
        setAuthToken(null)
        setUser(null)
      })
      .finally(() => setInitializing(false))
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const res = await apiLogin(username, password)
    localStorage.setItem(TOKEN_KEY, res.access_token)
    setAuthToken(res.access_token)
    setToken(res.access_token)
    setUser(res.user)
  }, [])

  const register = useCallback(async (username: string, password: string, email?: string) => {
    // Register trả user (không token) — tự login sau để có phiên.
    await apiRegister(username, password, email)
    await login(username, password)
  }, [login])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    setAuthToken(null)
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      initializing,
      isAdmin: user?.role === 'admin',
      login,
      register,
      logout,
    }),
    [user, token, initializing, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth phải được dùng bên trong <AuthProvider>.')
  return ctx
}

/** Chặn route cần đăng nhập — chưa login → chuyển về /login. */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, initializing } = useAuth()
  const location = useLocation()

  if (initializing) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-teal-600" />
      </div>
    )
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }
  return <>{children}</>
}

/** Chỉ admin — resident đã login nhưng không phải admin → về trang chủ. */
export function AdminRoute({ children }: { children: ReactNode }) {
  const { user, isAdmin, initializing } = useAuth()

  if (initializing) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-teal-600" />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  if (!isAdmin) return <Navigate to="/" replace />
  return <>{children}</>
}
