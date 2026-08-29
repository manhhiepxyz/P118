import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ArrowRight, Eye, EyeOff } from 'lucide-react'
import { GoogleLogin } from '@react-oauth/google'

import { useAuth } from '../lib/auth'
import { useToast } from '../lib/toast'

/**
 * Đăng nhập — cửa vào P-118.
 *
 * Dùng CHUNG hệ token với workspace (`.ws` trong `workspace.css`). Bản trước
 * dùng bảng màu riêng (`teal-700`, `gray-200`, `rounded-2xl`), nên màn hình đầu
 * tiên người dùng nhìn thấy thuộc về một sản phẩm khác với phần còn lại — và
 * chế độ tối thì trang này không có.
 *
 * Bố cục hai cột trên màn rộng: bên trái nói P-118 LÀM ĐƯỢC GÌ, bên phải là ô
 * đăng nhập. Người chưa có tài khoản cần lý do để đăng ký; một form trôi giữa
 * màn hình trắng không cho họ lý do nào. Dưới 1024px thì cột trái ẩn — trên
 * điện thoại, thứ duy nhất đáng chiếm màn hình là chính cái form.
 */
export function LoginPage() {
  const { login, googleLogin } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'
  const googleEnabled = Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID?.trim())

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

  const field =
    'mt-1.5 h-11 w-full rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--selection)]'

  return (
    <div className="ws min-h-dvh bg-[var(--surface-base)]">
      <div className="mx-auto flex min-h-dvh w-full max-w-[1080px] items-center px-6 py-10">
        <div className="grid w-full gap-14 lg:grid-cols-[1fr_400px]">
          {/* ── Cột trái: vì sao nên đăng nhập ─────────────────────── */}
          <section className="hidden self-center lg:block">
            <span
              className="inline-flex h-11 w-11 items-center justify-center rounded-[var(--r-md)] font-mono text-[15px] font-bold"
              style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
              aria-hidden
            >
              P
            </span>
            <h1 className="mt-6 text-[30px] font-semibold leading-[1.2] tracking-[-0.025em] text-[var(--text-primary)]">
              Nói việc bạn cần.
              <br />
              P-118 lo phần còn lại.
            </h1>
            <p className="mt-4 max-w-[46ch] text-[15px] leading-[1.65] text-[var(--text-secondary)]">
              Trợ lý dịch vụ cư dân: đặt lịch tham quan dự án, đăng ký nhận tư vấn, và sau khi xác
              minh căn hộ là đăng ký xe, chỗ đỗ, bảo trì, chuyển nhà.
            </p>

            {/* Liệt kê VIỆC, không liệt kê tính năng. Ba dòng này khớp
                `_CAPABILITY_CATALOGUE` phía backend — hứa nhiều hơn danh mục
                thì người dùng đăng nhập xong sẽ không tìm thấy thứ được hứa. */}
            <ul className="mt-8 space-y-3">
              {[
                'Đặt lịch tham quan dự án — chọn ngày giờ, kèm xe đưa đón nếu cần',
                'Đăng ký quan tâm / nhận tư vấn — để lại giờ tiện liên hệ',
                'Dịch vụ cư dân — mở sau khi căn hộ được xác minh',
              ].map((line) => (
                <li key={line} className="flex gap-3 text-[14.5px] leading-[1.6] text-[var(--text-secondary)]">
                  <span
                    className="mt-[9px] h-1 w-1 shrink-0 rounded-full"
                    style={{ backgroundColor: 'var(--agent)' }}
                    aria-hidden
                  />
                  {line}
                </li>
              ))}
            </ul>
          </section>

          {/* ── Cột phải: form ──────────────────────────────────────── */}
          <section className="w-full self-center">
            <div className="rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-7">
              {/* Logo chỉ hiện ở màn hẹp — màn rộng đã có ở cột trái, lặp lại
                  hai lần trong một tầm mắt là thừa. */}
              <span
                className="mb-5 inline-flex h-10 w-10 items-center justify-center rounded-[var(--r-md)] font-mono text-sm font-bold lg:hidden"
                style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
                aria-hidden
              >
                P
              </span>

              <h2 className="text-[19px] font-semibold tracking-[-0.015em] text-[var(--text-primary)]">
                Đăng nhập
              </h2>
              <p className="mt-1 text-[13.5px] text-[var(--text-muted)]">
                Dùng tài khoản cư dân của bạn.
              </p>

              <form onSubmit={handleSubmit} className="mt-6" noValidate>
                <label
                  htmlFor="login-username"
                  className="block text-[13px] font-medium text-[var(--text-secondary)]"
                >
                  Tên đăng nhập hoặc Email
                </label>
                <input
                  id="login-username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  autoFocus
                  placeholder="nguyen.van.a hoặc email"
                  className={field}
                />

                <label
                  htmlFor="login-password"
                  className="mt-4 block text-[13px] font-medium text-[var(--text-secondary)]"
                >
                  Mật khẩu
                </label>
                <div className="relative">
                  <input
                    id="login-password"
                    type={show ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    placeholder="••••••••"
                    className={`${field} pr-11`}
                  />
                  {/* Nút hiện/ẩn: 44px chiều cao chạm được, và có tên đọc
                      được — một con mắt không nói cho ai biết nó làm gì. */}
                  <button
                    type="button"
                    onClick={() => setShow((v) => !v)}
                    aria-label={show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
                    className="absolute bottom-0 right-0 flex h-11 w-11 cursor-pointer items-center justify-center text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
                  >
                    {show ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
                  </button>
                </div>
                <div className="mt-2 text-right">
                  <Link
                    to="/forgot-password"
                    className="text-[13px] font-medium text-[var(--agent)] hover:underline"
                    tabIndex={-1}
                  >
                    Quên mật khẩu?
                  </Link>
                </div>

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
                  disabled={submitting || !username.trim() || !password}
                  className="press mt-6 inline-flex h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-[var(--r-sm)] text-[14.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
                >
                  {submitting ? (
                    <>
                      <span
                        className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
                        aria-hidden
                      />
                      Đang đăng nhập…
                    </>
                  ) : (
                    <>
                      Đăng nhập
                      <ArrowRight className="h-4 w-4" strokeWidth={2.4} aria-hidden />
                    </>
                  )}
                </button>
                
                {googleEnabled && (
                  <>
                    <div className="mt-6 flex items-center justify-between">
                      <span className="w-1/5 border-b border-[var(--border-subtle)] lg:w-1/4"></span>
                      <span className="text-xs text-[var(--text-muted)] uppercase">Hoặc</span>
                      <span className="w-1/5 border-b border-[var(--border-subtle)] lg:w-1/4"></span>
                    </div>

                    <div className="mt-6 flex justify-center">
                      <GoogleLogin
                    onSuccess={async (credentialResponse) => {
                      if (!credentialResponse.credential) return;
                      try {
                        setSubmitting(true);
                        const res = await googleLogin(credentialResponse.credential);
                        if (res.status === 202) {
                          // Chuyển sang trang hoàn thiện thông tin
                          navigate('/google-register', { state: { credential: credentialResponse.credential, data: res.data } });
                        } else {
                          toast.push('success', 'Đăng nhập thành công!');
                          navigate(from, { replace: true });
                        }
                      } catch (err) {
                        const msg = err instanceof Error ? err.message : 'Không thể đăng nhập bằng Google.';
                        setError(msg);
                        toast.push('error', msg);
                        setSubmitting(false);
                      }
                    }}
                    onError={() => {
                      setError('Đăng nhập Google thất bại.');
                      toast.push('error', 'Đăng nhập Google thất bại.');
                    }}
                    useOneTap
                      />
                    </div>
                  </>
                )}

              </form>
            </div>

            <p className="mt-5 text-center text-[13.5px] text-[var(--text-muted)]">
              Chưa có tài khoản?{' '}
              <Link
                to="/register"
                className="font-medium text-[var(--agent)] underline-offset-2 hover:underline"
              >
                Đăng ký
              </Link>
            </p>
          </section>
        </div>
      </div>
    </div>
  )
}
