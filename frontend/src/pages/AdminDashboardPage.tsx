import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  Clock,
  Loader2,
  MessageCircleQuestion,
  TimerReset,
  XCircle,
} from 'lucide-react'

import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { adminMetrics } from '../lib/agentApi'
import { usePolling } from '../lib/usePolling'

/**
 * Quản trị — số liệu vận hành toàn hệ thống.
 *
 * Bản trước gọi `listWorkflows()` rồi hiển thị dưới tiêu đề "Giám sát toàn bộ
 * workflow". Endpoint đó lọc theo `owner_user_id`, và admin không sở hữu
 * workflow nào — nên màn hình luôn hiện 0 trong khi database có 92 workflow.
 * Admin nhìn thấy đúng view của một khách hàng trên dữ liệu rỗng của chính
 * mình, dán nhãn là giám sát toàn hệ thống.
 *
 * Giờ đọc `GET /admin/metrics` (`require_roles("admin")`), trả về đúng SỐ ĐẾM
 * trên toàn bộ bảng `workflows`.
 *
 * KHÔNG có bảng liệt kê từng workflow. Đó là chủ ý, không phải thiếu sót: bảng
 * cũ hiện `goal` của từng người — tức nội dung yêu cầu của cư dân — cho bất kỳ
 * ai có role admin. Giám sát vận hành cần biết CÓ BAO NHIÊU việc đang hỏng,
 * không cần biết AI yêu cầu GÌ. Một dashboard tiện tay hiển thị nội dung yêu
 * cầu là một cách rò rỉ dữ liệu được cấp phép sẵn.
 */

const CARDS: {
  key: 'total' | 'running' | 'waiting_approval' | 'success' | 'failed' | 'cancelled' | 'awaiting_user' | 'orphaned'
  label: string
  hint: string
  Icon: typeof Building2
  token: string
}[] = [
  { key: 'total', label: 'Tổng yêu cầu', hint: 'Chưa lưu trữ', Icon: Building2, token: 'var(--text-secondary)' },
  { key: 'running', label: 'Đang chạy', hint: 'Đã nhận, chưa xong', Icon: Loader2, token: 'var(--running)' },
  {
    key: 'waiting_approval',
    label: 'Chờ xác nhận',
    hint: 'Chờ người hoặc đơn vị quyết',
    Icon: Clock,
    token: 'var(--waiting-provider)',
  },
  { key: 'success', label: 'Hoàn tất', hint: 'Đã thực hiện xong', Icon: CheckCircle2, token: 'var(--success)' },
  { key: 'failed', label: 'Thất bại', hint: 'Dừng giữa chừng', Icon: AlertTriangle, token: 'var(--danger)' },
  { key: 'cancelled', label: 'Đã huỷ', hint: 'Người dùng hoặc đơn vị từ chối', Icon: XCircle, token: 'var(--text-muted)' },
  {
    key: 'awaiting_user',
    label: 'Chờ khách trả lời',
    hint: 'Thiếu thông tin, hệ thống đang đợi',
    Icon: MessageCircleQuestion,
    token: 'var(--waiting-user)',
  },
  {
    // Khác 0 = vòng quét zombie đang không chạy. Đây là chỉ số về HỆ THỐNG,
    // không phải về người dùng — và là con số duy nhất trên trang này đáng để
    // ai đó thức dậy lúc nửa đêm.
    key: 'orphaned',
    label: 'Mồ côi',
    hint: 'Quá hạn mà chưa được dọn — kiểm tra vòng quét',
    Icon: TimerReset,
    token: 'var(--danger)',
  },
]

export function AdminDashboardPage() {
  const { data, loading, error } = usePolling(adminMetrics, 10000)

  return (
    <WorkspaceShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-[1000px] px-10 pb-20 pt-12">
          <p className="font-mono text-[12px] font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">
            Quản trị
          </p>
          <h1 className="mt-4 text-[30px] font-semibold leading-[1.15] tracking-[-0.025em] text-[var(--text-primary)]">
            Vận hành hệ thống
          </h1>
          <p className="mt-3 max-w-[58ch] text-[15.5px] leading-[1.65] text-[var(--text-secondary)]">
            Số liệu trên toàn bộ yêu cầu, cập nhật mỗi 10 giây.
          </p>

          {error && (
            <p
              className="mt-8 rounded-[var(--r-sm)] px-4 py-3 text-[14px]"
              style={{
                color: 'var(--danger)',
                backgroundColor: 'color-mix(in srgb, var(--danger) 8%, transparent)',
              }}
              role="alert"
            >
              Chưa đọc được số liệu. Kiểm tra kết nối rồi thử lại.
            </p>
          )}

          <div className="mt-9 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {CARDS.map(({ key, label, hint, Icon, token }) => (
              <div
                key={key}
                className="rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-5"
              >
                <span
                  className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)]"
                  style={{ backgroundColor: `color-mix(in srgb, ${token} 12%, transparent)` }}
                  aria-hidden
                >
                  <Icon className="h-[18px] w-[18px]" strokeWidth={2} style={{ color: token }} />
                </span>
                {/* Chỗ trống giữ đúng chiều cao lúc đang tải, để lưới không
                    nhảy khi số đầu tiên về. */}
                <p
                  className="mt-4 text-[30px] font-semibold leading-none tabular-nums text-[var(--text-primary)]"
                  aria-live="polite"
                >
                  {loading && !data ? '—' : (data?.[key] ?? 0)}
                </p>
                <p className="mt-2 text-[14px] font-medium text-[var(--text-primary)]">{label}</p>
                <p className="mt-0.5 text-[12.5px] text-[var(--text-muted)]">{hint}</p>
              </div>
            ))}
          </div>

          {/* Nói RÕ vì sao không có bảng chi tiết. Một khoảng trống không giải
              thích đọc như tính năng chưa làm xong. */}
          <p className="mt-10 max-w-[62ch] text-[14px] leading-[1.65] text-[var(--text-muted)]">
            Trang này cố ý không liệt kê từng yêu cầu. Giám sát vận hành cần biết có bao nhiêu việc
            đang hỏng, không cần biết ai yêu cầu gì — nội dung yêu cầu của cư dân không đi qua đây.
            Hồ sơ cần duyệt nằm ở cổng xác thực của đơn vị.
          </p>
        </div>
      </div>
    </WorkspaceShell>
  )
}
