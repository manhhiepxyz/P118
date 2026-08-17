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

import {
  getMe,
  getStoredToken,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  type RegisterProfileInput,
} from './agentApi'
import type { AuthUser } from './types'

/* ---------------------------------------------------------------------------
   AuthContext — giữ trạng thái đăng nhập (user + token) cho toàn app.

   - login/register: gọi `agentApi` — API thật, không còn facade mock.
   - Token nằm trong sessionStorage (do `agentApi` quản), KHÔNG localStorage:
     token sống tới 24h, và localStorage tồn tại qua cả lần đóng trình duyệt,
     nên trên máy dùng chung một tab đóng lại vẫn để nguyên phiên cho người sau.
     PRODUCTION nên dùng cookie HttpOnly + refresh flow; chưa làm ở Gate 2.
   - Khởi tạo: có token → gọi getMe để khôi phục user + trạng thái liên kết cư dân.
   - ProtectedRoute: chặn route cần đăng nhập; AdminRoute: chỉ admin.

   `isAdmin` chỉ nói về VAI TRÒ TÀI KHOẢN. Quyền dùng dịch vụ cư dân nằm ở
   `user.resident_verification_status` — hai trục độc lập, và admin không tự
   động là chủ căn hộ nào.
--------------------------------------------------------------------------- */

interface AuthContextValue {
  user: AuthUser | null
  token: string | null
  initializing: boolean
  isAdmin: boolean
  /** provider hoặc admin — cả hai được duyệt hồ sơ xác thực. */
  isProvider: boolean
  login: (username: string, password: string) => Promise<void>
  register: (
    username: string,
    password: string,
    email?: string,
    profile?: RegisterProfileInput,
  ) => Promise<void>
  /** Đọc lại user qua /auth/me — dùng sau PATCH /users/me để UI cập nhật ngay. */
  refreshUser: () => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [initializing, setInitializing] = useState(true)

  // Khôi phục phiên khi app load.
  useEffect(() => {
    const stored = getStoredToken()
    if (!stored) {
      setInitializing(false)
      return
    }
    setToken(stored)
    getMe()
      .then((u) => setUser(u))
      .catch(() => {
        // Token cũ/hết hạn — `agentApi` đã xoá nó khi gặp 401.
        apiLogout()
        setToken(null)
        setUser(null)
      })
      .finally(() => setInitializing(false))
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const res = await apiLogin(username, password)
    setToken(res.access_token)
    // Đọc lại qua /auth/me: response login chưa mang trạng thái liên kết cư
    // dân, mà UI cần nó ngay để biết dịch vụ nào đang mở.
    setUser(await getMe())
  }, [])

  const register = useCallback(
    async (
      username: string,
      password: string,
      email?: string,
      profile?: RegisterProfileInput,
    ) => {
      // Register trả user (không token) — tự login sau để có phiên.
      await apiRegister(username, password, email, profile)
      await login(username, password)
    },
    [login],
  )

  const refreshUser = useCallback(async () => {
    const u = await getMe()
    setUser(u)
  }, [])

  const logout = useCallback(() => {
    apiLogout()
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      initializing,
      isAdmin: user?.role === 'admin',
      isProvider: user?.role === 'provider' || user?.role === 'admin',
      login,
      register,
      refreshUser,
      logout,
    }),
    [user, token, initializing, login, register, refreshUser, logout],
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

/** Chỉ admin — customer đã login nhưng không phải admin → về trang chủ. */
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

/** Chỉ người duyệt hồ sơ xác thực — provider hoặc admin. */
export function ProviderRoute({ children }: { children: ReactNode }) {
  const { user, isProvider, initializing } = useAuth()

  if (initializing) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-teal-600" />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  if (!isProvider) return <Navigate to="/" replace />
  return <>{children}</>
}
