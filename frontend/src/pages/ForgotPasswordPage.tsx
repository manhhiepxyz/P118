import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Eye, EyeOff } from 'lucide-react'

import { forgotPassword, resetPassword } from '../lib/agentApi'
import { useToast } from '../lib/toast'

const OTP_LIFETIME_SECONDS = 5 * 60
const OTP_RESEND_COOLDOWN_SECONDS = 60

function remainingSeconds(deadline: number | null): number {
  if (deadline === null) return 0
  return Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
}

function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
}

export function ForgotPasswordPage() {
  const toast = useToast()
  const navigate = useNavigate()

  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [email, setEmail] = useState('')
  const [otpCode, setOtpCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [show, setShow] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)

  const [submitting, setSubmitting] = useState(false)
  const [resending, setResending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [otpExpiresAt, setOtpExpiresAt] = useState<number | null>(null)
  const [resendAvailableAt, setResendAvailableAt] = useState<number | null>(null)
  const [otpSecondsLeft, setOtpSecondsLeft] = useState(0)
  const [resendSecondsLeft, setResendSecondsLeft] = useState(0)

  useEffect(() => {
    if (step === 1) return

    const updateCountdowns = () => {
      setOtpSecondsLeft(remainingSeconds(otpExpiresAt))
      setResendSecondsLeft(remainingSeconds(resendAvailableAt))
    }

    updateCountdowns()
    const timer = window.setInterval(updateCountdowns, 1000)
    return () => window.clearInterval(timer)
  }, [otpExpiresAt, resendAvailableAt, step])

  function startOtpCountdowns() {
    const now = Date.now()
    const expiresAt = now + OTP_LIFETIME_SECONDS * 1000
    const resendAt = now + OTP_RESEND_COOLDOWN_SECONDS * 1000
    setOtpExpiresAt(expiresAt)
    setResendAvailableAt(resendAt)
    setOtpSecondsLeft(OTP_LIFETIME_SECONDS)
    setResendSecondsLeft(OTP_RESEND_COOLDOWN_SECONDS)
  }

  async function handleSendOtp(e: FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError(null)

    const emailTrimmed = email.trim()
    if (!emailTrimmed) {
      setError('Vui lòng nhập địa chỉ email.')
      return
    }

    setSubmitting(true)
    try {
      await forgotPassword(emailTrimmed)
      startOtpCountdowns()
      setStep(2)
      toast.push('success', 'Mã xác nhận đã được gửi nếu email tồn tại.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Có lỗi xảy ra, vui lòng thử lại.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleResendOtp() {
    if (resending || resendSecondsLeft > 0) return
    setResending(true)
    setError(null)

    try {
      await forgotPassword(email.trim())
      startOtpCountdowns()
      toast.push('success', 'Đã gửi lại mã xác nhận mới.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không thể gửi lại mã.')
    } finally {
      setResending(false)
    }
  }

  function handleVerifyOtp(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (otpCode.trim().length !== 6) {
      setError('Mã OTP phải gồm 6 chữ số.')
      return
    }
    // Chuyển sang bước 3 (nhập mật khẩu mới), OTP sẽ được kiểm tra ở backend lúc đổi mật khẩu
    setStep(3)
  }

  async function handleResetPassword(e: FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError(null)

    if (newPassword.length < 8) {
      setError('Mật khẩu mới phải có ít nhất 8 ký tự.')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('Mật khẩu nhập lại không khớp.')
      return
    }

    setSubmitting(true)
    try {
      await resetPassword(email.trim(), otpCode.trim(), newPassword)
      toast.push('success', 'Đặt lại mật khẩu thành công! Bạn có thể đăng nhập ngay.')
      navigate('/login', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không thể đặt lại mật khẩu.')
    } finally {
      setSubmitting(false)
    }
  }

  const field =
    'mt-1.5 h-11 w-full rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--selection)]'

  return (
    <div className="ws min-h-dvh bg-[var(--surface-base)]">
      <div className="mx-auto flex min-h-dvh w-full max-w-[500px] items-center px-6 py-10">
        <section className="w-full self-center">
          <div className="rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-7">
            <span
              className="mb-5 inline-flex h-10 w-10 items-center justify-center rounded-[var(--r-md)] font-mono text-sm font-bold"
              style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
              aria-hidden
            >
              P
            </span>

            <h2 className="text-[19px] font-semibold tracking-[-0.015em] text-[var(--text-primary)]">
              Quên mật khẩu
            </h2>
            <p className="mt-1 text-[13.5px] text-[var(--text-muted)]">
              {step === 1 && 'Nhập email để nhận mã OTP.'}
              {step === 2 && 'Nhập mã OTP.'}
              {step === 3 && 'Nhập mật khẩu mới.'}
            </p>

            {step === 1 && (
              <form onSubmit={handleSendOtp} className="mt-6" noValidate>
                <label
                  htmlFor="forgot-email"
                  className="block text-[13px] font-medium text-[var(--text-secondary)]"
                >
                  Email
                </label>
                <input
                  id="forgot-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="nguyen.van.a@example.com"
                  autoFocus
                  className={field}
                />

                {error && (
                  <div className="mt-4 rounded-[var(--r-sm)] bg-red-50 p-3 text-[13px] text-red-800 dark:bg-red-950/30 dark:text-red-400">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={submitting}
                  className="mt-6 flex h-11 w-full items-center justify-center rounded-[var(--r-sm)] text-[14.5px] font-medium transition-all disabled:opacity-50"
                  style={{
                    backgroundColor: 'var(--agent)',
                    color: 'var(--surface-base)',
                  }}
                >
                  {submitting ? 'Đang gửi...' : 'Gửi mã OTP'}
                </button>
              </form>
            )}

            {step === 2 && (
              <form onSubmit={handleVerifyOtp} className="mt-6" noValidate>
                <div className="rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] p-4 text-[13.5px] leading-relaxed text-[var(--text-secondary)]">
                  Mã xác nhận gồm 6 chữ số đã được gửi tới <strong>{email.trim()}</strong>. Mã
                  {otpSecondsLeft > 0
                    ? ` còn hiệu lực ${formatCountdown(otpSecondsLeft)}.`
                    : ' đã hết hiệu lực.'}{' '}
                  Vui lòng kiểm tra hộp thư.
                </div>

                <div className="mt-6">
                  <label
                    htmlFor="reset-otp"
                    className="block text-[13px] font-medium text-[var(--text-secondary)]"
                  >
                    Mã OTP
                  </label>
                  <input
                    id="reset-otp"
                    value={otpCode}
                    onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ''))}
                    maxLength={6}
                    autoComplete="one-time-code"
                    placeholder="123456"
                    autoFocus
                    className={field}
                  />
                </div>

                {error && (
                  <div className="mt-4 rounded-[var(--r-sm)] bg-red-50 p-3 text-[13px] text-red-800 dark:bg-red-950/30 dark:text-red-400">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  className="mt-6 flex h-11 w-full items-center justify-center rounded-[var(--r-sm)] text-[14.5px] font-medium transition-all"
                  style={{
                    backgroundColor: 'var(--agent)',
                    color: 'var(--surface-base)',
                  }}
                >
                  Xác nhận mã
                </button>

                <div className="mt-6 border-t border-[var(--border-subtle)] pt-6 text-center text-[13px] text-[var(--text-secondary)]">
                  Chưa nhận được mã?{' '}
                  <button
                    type="button"
                    onClick={handleResendOtp}
                    disabled={resending || resendSecondsLeft > 0}
                    className="font-medium text-[var(--text-primary)] disabled:opacity-50"
                  >
                    {resendSecondsLeft > 0
                      ? `Gửi lại sau ${formatCountdown(resendSecondsLeft)}`
                      : 'Gửi lại mã'}
                  </button>
                </div>
              </form>
            )}

            {step === 3 && (
              <form onSubmit={handleResetPassword} className="mt-6" noValidate>
                <label
                  htmlFor="reset-password"
                  className="block text-[13px] font-medium text-[var(--text-secondary)]"
                >
                  Mật khẩu mới
                </label>
                <div className="relative">
                  <input
                    id="reset-password"
                    type={show ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="Ít nhất 8 ký tự"
                    autoFocus
                    className={`${field} pr-11`}
                  />
                  <button
                    type="button"
                    onClick={() => setShow(!show)}
                    className="absolute bottom-0 right-0 flex h-11 w-11 items-center justify-center text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
                    tabIndex={-1}
                  >
                    {show ? <EyeOff className="h-[18px] w-[18px]" /> : <Eye className="h-[18px] w-[18px]" />}
                  </button>
                </div>

                <label
                  htmlFor="confirm-password"
                  className="mt-4 block text-[13px] font-medium text-[var(--text-secondary)]"
                >
                  Xác nhận mật khẩu mới
                </label>
                <div className="relative">
                  <input
                    id="confirm-password"
                    type={showConfirm ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Nhập lại mật khẩu"
                    className={`${field} pr-11`}
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirm(!showConfirm)}
                    className="absolute bottom-0 right-0 flex h-11 w-11 items-center justify-center text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
                    tabIndex={-1}
                  >
                    {showConfirm ? <EyeOff className="h-[18px] w-[18px]" /> : <Eye className="h-[18px] w-[18px]" />}
                  </button>
                </div>

                {error && (
                  <div className="mt-4 rounded-[var(--r-sm)] bg-red-50 p-3 text-[13px] text-red-800 dark:bg-red-950/30 dark:text-red-400">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={submitting}
                  className="mt-6 flex h-11 w-full items-center justify-center rounded-[var(--r-sm)] text-[14.5px] font-medium transition-all disabled:opacity-50"
                  style={{
                    backgroundColor: 'var(--agent)',
                    color: 'var(--surface-base)',
                  }}
                >
                  {submitting ? 'Đang xử lý...' : 'Đặt lại mật khẩu'}
                </button>
              </form>
            )}

            <div className="mt-8 text-center text-[13.5px] text-[var(--text-secondary)]">
              Quay lại <Link to="/login" className="font-medium text-[var(--agent)] hover:underline">Đăng nhập</Link>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
