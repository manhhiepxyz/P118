import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'

import { useAuth } from '../lib/auth'
import { useToast } from '../lib/toast'

export function GoogleRegisterPage() {
  const { googleRegister } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const location = useLocation()
  
  const state = location.state as { credential?: string; data?: { email: string, name: string, picture: string } } | null
  
  if (!state?.credential || !state?.data) {
    return <Navigate to="/login" replace />
  }

  // Khởi tạo username từ phần trước của email
  const defaultUsername = state.data.email.split('@')[0].replace(/[^a-zA-Z0-9]/g, '')
  
  const [username, setUsername] = useState(defaultUsername)
  const [phone, setPhone] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!username.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    
    try {
      await googleRegister(state!.credential!, username.trim(), phone.trim() || undefined)
      toast.push('success', `Chào mừng, ${username.trim()}!`)
      navigate('/', { replace: true })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Không thể hoàn tất đăng ký.'
      setError(msg)
      toast.push('error', msg)
      setSubmitting(false)
    }
  }

  const field =
    'mt-1.5 h-11 w-full rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--selection)]'

  return (
    <div className="ws min-h-dvh bg-[var(--surface-base)] flex items-center justify-center py-10 px-6">
      <div className="w-full max-w-md rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-7">
        <div className="flex justify-center mb-4">
            {state.data.picture && (
              <img src={state.data.picture} alt="Avatar" className="w-16 h-16 rounded-full border-2 border-[var(--border-subtle)]" />
            )}
        </div>
        <h2 className="text-center text-[19px] font-semibold tracking-[-0.015em] text-[var(--text-primary)]">
          Hoàn thiện thông tin
        </h2>
        <p className="mt-1 text-center text-[13.5px] text-[var(--text-muted)]">
          Xin chào <strong>{state.data.name}</strong>, vui lòng chọn tên đăng nhập để tiếp tục.
        </p>

        <form onSubmit={handleSubmit} className="mt-6" noValidate>
          <label
            htmlFor="username"
            className="block text-[13px] font-medium text-[var(--text-secondary)]"
          >
            Tên đăng nhập <span className="text-[var(--danger)]">*</span>
          </label>
          <input
            id="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            className={field}
          />
          <p className="mt-1 text-xs text-[var(--text-muted)]">Tên dùng để đăng nhập hệ thống sau này.</p>

          <label
            htmlFor="phone"
            className="mt-4 block text-[13px] font-medium text-[var(--text-secondary)]"
          >
            Số điện thoại (Không bắt buộc)
          </label>
          <input
            id="phone"
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            autoComplete="tel"
            placeholder="0912345678"
            className={field}
          />

          {error && (
            <p
              className="mt-4 rounded-[var(--r-sm)] px-3.5 py-2.5 text-[13.5px] leading-[1.5]"
              style={{
                color: 'var(--danger)',
                backgroundColor: 'color-mix(in srgb, var(--danger) 8%, transparent)',
              }}
              role="alert"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting || !username.trim()}
            className="press mt-6 inline-flex h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-[var(--r-sm)] text-[14.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
            style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
          >
            {submitting ? (
              <>
                <span
                  className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
                  aria-hidden
                />
                Đang xử lý…
              </>
            ) : (
              <>
                Hoàn tất
                <ArrowRight className="h-4 w-4" strokeWidth={2.4} aria-hidden />
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  )
}
