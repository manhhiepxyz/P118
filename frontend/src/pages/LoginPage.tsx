import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Eye, EyeOff, LogIn, Sparkles } from 'lucide-react'

import { useAuth } from '../lib/auth'
import { useToast } from '../lib/toast'

/** Login — màn hình đăng nhập (Prompt 5.1). */
export function LoginPage() {
  const { login } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!username.trim() || !password || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await login(username.trim(), password)
      toast.push('success', `Chào mừng, ${username.trim()}!`)
      navigate(from, { replace: true })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Không thể đăng nhập.'
      setError(msg)
      toast.push('error', msg)
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="rounded-2xl border border-gray-200 bg-card p-8 shadow-sm">
          <div className="flex flex-col items-center text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-teal-700 text-lg font-bold text-white shadow-sm">
              P
            </span>
            <h1 className="mt-4 text-xl font-semibold text-gray-900">Đăng nhập</h1>
            <p className="mt-1 text-sm text-gray-500">
              P-118 — Trợ lý dịch vụ cư dân
            </p>
          </div>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
            <div>
              <label htmlFor="login-username" className="mb-1 block text-sm font-medium text-gray-700">
                Tên đăng nhập
              </label>
              <input
                id="login-username"
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="nguyen.van.a"
                className="w-full rounded-xl border border-gray-300 bg-card px-3.5 py-2.5 text-sm text-gray-900 shadow-sm outline-none placeholder:text-gray-300 focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              />
            </div>

            <div>
              <label htmlFor="login-password" className="mb-1 block text-sm font-medium text-gray-700">
                Mật khẩu
              </label>
              <div className="relative">
                <input
                  id="login-password"
                  type={show ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full rounded-xl border border-gray-300 bg-card px-3.5 py-2.5 pr-10 text-sm text-gray-900 shadow-sm outline-none placeholder:text-gray-300 focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                />
                <button
                  type="button"
                  aria-label={show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
                  onClick={() => setShow((v) => !v)}
                  className="absolute inset-y-0 right-0 flex items-center px-3 text-gray-400 hover:text-gray-600"
                >
                  {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {error && (
              <p className="rounded-xl bg-red-50 px-4 py-2.5 text-sm text-red-700" role="alert">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={!username.trim() || !password || submitting}
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
              ) : (
                <LogIn className="h-4 w-4" aria-hidden />
              )}
              {submitting ? 'Đang đăng nhập…' : 'Đăng nhập'}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-gray-500">
            Chưa có tài khoản?{' '}
            <Link to="/register" className="font-medium text-teal-700 hover:underline">
              Đăng ký
            </Link>
          </p>
        </div>


        <p className="mt-4 flex items-center justify-center gap-1.5 text-center text-xs text-gray-400">
          <Sparkles className="h-3.5 w-3.5" aria-hidden />
          AI Agent điều phối đa dịch vụ cho cư dân
        </p>
      </div>
    </div>
  )
}
