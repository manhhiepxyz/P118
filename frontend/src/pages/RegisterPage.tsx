import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Eye, EyeOff, UserPlus, Mail } from 'lucide-react'

import { useAuth } from '../lib/auth'
import { useToast } from '../lib/toast'
import { sendOtp } from '../lib/agentApi'

/** Register — tạo tài khoản cư dân có OTP. */
export function RegisterPage() {
  const { register } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()

  // Bước 1
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  
  // Bước 2
  const [otpCode, setOtpCode] = useState('')

  // UI state
  const [step, setStep] = useState<1 | 2>(1)
  const [show, setShow] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Hồ sơ tự khai — optional
  const [fullName, setFullName] = useState('')
  const [phone, setPhone] = useState('')
  const [address, setAddress] = useState('')
  const [dob, setDob] = useState('')
  const [gender, setGender] = useState('')
  const [cccdLast4, setCccdLast4] = useState('')

  async function handleSendOtp(e: FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError(null)

    if (username.trim().length < 3) {
      setError('Tên đăng nhập phải ít nhất 3 ký tự.')
      return
    }
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
    if (!emailRegex.test(email.trim())) {
      setError('Định dạng email không hợp lệ.')
      return
    }
    if (password.length < 8) {
      setError('Mật khẩu phải ít nhất 8 ký tự.')
      return
    }
    if (password !== confirm) {
      setError('Mật khẩu xác nhận không khớp.')
      return
    }

    setSubmitting(true)
    try {
      const payload = {
        username: username.trim(),
        password,
        email: email.trim(),
        full_name: fullName.trim() || undefined,
        phone: phone.trim() || undefined,
        address: address.trim() || undefined,
        date_of_birth: dob || undefined,
        gender: gender || undefined,
        cccd_last4: cccdLast4.replace(/\D/g, '').slice(0, 4) || undefined,
      }
      await sendOtp(payload)
      toast.push('success', 'Mã OTP đã được gửi đến email của bạn!')
      setStep(2)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Không thể gửi OTP.'
      setError(msg)
      toast.push('error', msg)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRegister(e: FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError(null)

    if (otpCode.trim().length !== 6) {
      setError('Mã OTP phải bao gồm 6 chữ số.')
      return
    }

    setSubmitting(true)
    try {
      await register(username.trim(), password, email.trim(), otpCode.trim(), {
        full_name: fullName.trim() || undefined,
        phone: phone.trim() || undefined,
        address: address.trim() || undefined,
        date_of_birth: dob || undefined,
        gender: gender || undefined,
        cccd_last4: cccdLast4.replace(/\D/g, '').slice(0, 4) || undefined,
      })
      toast.push('success', 'Tạo tài khoản thành công!')
      navigate('/', { replace: true })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Không thể đăng ký.'
      setError(msg)
      toast.push('error', msg)
      setSubmitting(false)
    }
  }

  const inputClass =
    'w-full rounded-xl border border-gray-300 bg-card px-3.5 py-2.5 text-sm text-gray-900 shadow-sm outline-none placeholder:text-gray-300 focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20'

  return (
    <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="rounded-2xl border border-gray-200 bg-card p-8 shadow-sm">
          <div className="flex flex-col items-center text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-teal-700 text-lg font-bold text-white shadow-sm">
              P
            </span>
            <h1 className="mt-4 text-xl font-semibold text-gray-900">Tạo tài khoản</h1>
            <p className="mt-1 text-sm text-gray-500">
              {step === 1 ? 'Đăng ký để sử dụng trợ lý dịch vụ cư dân' : 'Xác thực địa chỉ email của bạn'}
            </p>
          </div>

          {step === 1 ? (
            <form onSubmit={handleSendOtp} className="mt-6 space-y-4" noValidate>
              <div>
                <label htmlFor="reg-username" className="mb-1 block text-sm font-medium text-gray-700">
                  Tên đăng nhập <span className="font-medium text-red-500">*</span>
                </label>
                <input
                  id="reg-username"
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="nguyen.van.a"
                  className={inputClass}
                />
              </div>

              <div>
                <label htmlFor="reg-email" className="mb-1 block text-sm font-medium text-gray-700">
                  Email <span className="font-medium text-red-500">*</span>
                </label>
                <input
                  id="reg-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="batbuoc@example.com"
                  className={inputClass}
                />
              </div>

              <div>
                <label htmlFor="reg-password" className="mb-1 block text-sm font-medium text-gray-700">
                  Mật khẩu <span className="font-medium text-red-500">*</span>
                </label>
                <div className="relative">
                  <input
                    id="reg-password"
                    type={show ? 'text' : 'password'}
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Ít nhất 8 ký tự"
                    className={`${inputClass} pr-10`}
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

              <div>
                <label htmlFor="reg-confirm" className="mb-1 block text-sm font-medium text-gray-700">
                  Xác nhận mật khẩu <span className="font-medium text-red-500">*</span>
                </label>
                <input
                  id="reg-confirm"
                  type={show ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder="Nhập lại mật khẩu"
                  className={inputClass}
                />
              </div>

              {/* Hồ sơ tự khai — tất cả không bắt buộc, có thể khai sau ở trang Hồ sơ. */}
              <div className="border-t border-gray-100 pt-4 dark:border-gray-800">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  Thông tin thêm <span className="font-normal text-gray-400">(không bắt buộc)</span>
                </p>
                <div className="mt-3 space-y-4">
                  <div>
                    <label htmlFor="reg-name" className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                      Họ và tên
                    </label>
                    <input
                      id="reg-name"
                      type="text"
                      autoComplete="name"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      placeholder="Nguyễn Văn A"
                      className={inputClass}
                    />
                  </div>

                  <div>
                    <label htmlFor="reg-phone" className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                      Số điện thoại
                    </label>
                    <input
                      id="reg-phone"
                      type="tel"
                      autoComplete="tel"
                      value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      placeholder="0981 234 567"
                      className={inputClass}
                    />
                  </div>

                  <div>
                    <label htmlFor="reg-address" className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                      Địa chỉ
                    </label>
                    <input
                      id="reg-address"
                      type="text"
                      autoComplete="street-address"
                      value={address}
                      onChange={(e) => setAddress(e.target.value)}
                      placeholder="Số nhà, phường, quận…"
                      className={inputClass}
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label htmlFor="reg-dob" className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                        Ngày sinh
                      </label>
                      <input
                        id="reg-dob"
                        type="date"
                        value={dob}
                        onChange={(e) => setDob(e.target.value)}
                        className={inputClass}
                      />
                    </div>
                    <div>
                      <label htmlFor="reg-gender" className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                        Giới tính
                      </label>
                      <select
                        id="reg-gender"
                        value={gender}
                        onChange={(e) => setGender(e.target.value)}
                        className={inputClass}
                      >
                        <option value="">—</option>
                        <option value="nam">Nam</option>
                        <option value="nu">Nữ</option>
                        <option value="khac">Khác</option>
                      </select>
                    </div>
                  </div>

                  <div>
                    <label htmlFor="reg-cccd" className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                      4 số cuối CCCD
                    </label>
                    <input
                      id="reg-cccd"
                      type="text"
                      inputMode="numeric"
                      maxLength={4}
                      value={cccdLast4}
                      onChange={(e) =>
                        setCccdLast4(e.target.value.replace(/\D/g, '').slice(0, 4))
                      }
                      placeholder="Chỉ 4 số cuối"
                      className={inputClass}
                    />
                    <p className="mt-1 text-xs text-gray-400">
                      Hệ thống chỉ lưu 4 số cuối để nhận diện hồ sơ, không lưu số CCCD đầy đủ.
                    </p>
                  </div>
                </div>
              </div>

              {error && (
                <p className="rounded-xl bg-red-50 px-4 py-2.5 text-sm text-red-700" role="alert">
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={!username.trim() || !email.trim() || !password || !confirm || submitting}
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? (
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                ) : (
                  <Mail className="h-4 w-4" aria-hidden />
                )}
                {submitting ? 'Đang xử lý…' : 'Đăng ký'}
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="mt-6 space-y-4" noValidate>
              <div className="rounded-xl bg-teal-50 p-4 text-sm text-teal-800 mb-4">
                Mã xác nhận 6 số đã được gửi tới <strong>{email}</strong>. Vui lòng kiểm tra hộp thư (kể cả mục Spam).
              </div>

              <div>
                <label htmlFor="reg-otp" className="mb-1 block text-sm font-medium text-gray-700">
                  Mã OTP <span className="font-medium text-red-500">*</span>
                </label>
                <input
                  id="reg-otp"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  value={otpCode}
                  onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ''))}
                  placeholder="123456"
                  className={`${inputClass} text-center tracking-widest text-lg font-bold`}
                  autoFocus
                />
              </div>

              {error && (
                <p className="rounded-xl bg-red-50 px-4 py-2.5 text-sm text-red-700" role="alert">
                  {error}
                </p>
              )}

              <div className="flex gap-3">
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => {
                    setStep(1)
                    setError(null)
                  }}
                  className="inline-flex w-1/3 items-center justify-center rounded-xl border border-gray-300 bg-white px-4 py-2.5 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Quay lại
                </button>
                <button
                  type="submit"
                  disabled={otpCode.length !== 6 || submitting}
                  className="inline-flex w-2/3 items-center justify-center gap-2 rounded-xl bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {submitting ? (
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                  ) : (
                    <UserPlus className="h-4 w-4" aria-hidden />
                  )}
                  {submitting ? 'Đang tạo…' : 'Tạo tài khoản'}
                </button>
              </div>
            </form>
          )}

          <p className="mt-6 text-center text-sm text-gray-500">
            Đã có tài khoản?{' '}
            <Link to="/login" className="font-medium text-teal-700 hover:underline">
              Đăng nhập
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
