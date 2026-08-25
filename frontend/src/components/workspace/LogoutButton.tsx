import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'

import { useAuth } from '../../lib/auth'
import { useToast } from '../../lib/toast'

/**
 * Lối ra, ở góc phải trên của workspace.
 *
 * Chỗ này từng là chỉ báo "P-118 · Sẵn sàng" — một dòng trang trí đọc dữ liệu
 * giả, chiếm vị trí đắt nhất màn hình, trong khi người dùng không có chỗ nào để
 * đăng xuất.
 *
 * Hai quyết định:
 *
 *  - **Hiện tên tài khoản.** Đăng xuất là hành động không hoàn tác trong một
 *    cú bấm; biết mình đang là ai trước khi bấm là điều tối thiểu. Trên màn
 *    hẹp thì ẩn tên, giữ lại biểu tượng và nhãn đọc được cho trình đọc màn hình.
 *  - **KHÔNG hỏi xác nhận.** Đăng xuất không mất dữ liệu — mọi workflow đã nằm
 *    trong database và còn nguyên khi đăng nhập lại. Một hộp thoại ở đây chỉ
 *    thêm một lần bấm cho việc an toàn.
 */
export function LogoutButton() {
  const { user, logout } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const [leaving, setLeaving] = useState(false)

  return (
    <button
      type="button"
      disabled={leaving}
      onClick={() => {
        setLeaving(true)
        logout()
        toast.push('info', 'Đã đăng xuất.')
        navigate('/login')
      }}
      aria-label={user?.username ? `Đăng xuất khỏi ${user.username}` : 'Đăng xuất'}
      className="press inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] py-1 pl-3 pr-2.5 text-[13px] font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-60"
    >
      {user?.username && (
        <span className="hidden max-w-[140px] truncate sm:inline">{user.username}</span>
      )}
      <LogOut className="h-3.5 w-3.5 shrink-0" strokeWidth={2.2} aria-hidden />
    </button>
  )
}
