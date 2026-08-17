import { Clock3, CreditCard, ListChecks } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type GateKind = 'payment' | 'provider' | 'plan'

interface Props {
  kind: GateKind
  title: string
  /** Dòng chính — số tiền, thời điểm gửi, hoặc số việc sẽ làm. */
  headline?: string
  /** Câu nói rõ người dùng cần làm gì, hoặc không cần làm gì. */
  hint: string
  /** Không truyền = không có nút. Đây CHÍNH LÀ điểm khác của `provider`. */
  actions?: React.ReactNode
  children?: React.ReactNode
}

const GATE_VIEW: Record<GateKind, { Icon: LucideIcon; frame: string; accent: string }> = {
  // Cần bạn quyết, có tiền → vàng, nổi bật nhất.
  payment: {
    Icon: CreditCard,
    frame: 'border-amber-300 bg-amber-50/70 dark:border-amber-700/60 dark:bg-amber-950/30',
    accent: 'text-amber-700 dark:text-amber-300',
  },
  // Chờ NGƯỜI KHÁC → xám, cố ý trầm để không mời gọi thao tác.
  provider: {
    Icon: Clock3,
    frame: 'border-gray-300 bg-gray-50 dark:border-gray-700 dark:bg-gray-100/5',
    accent: 'text-gray-600 dark:text-gray-400',
  },
  // Cần bạn duyệt kế hoạch, chưa có gì xảy ra → teal thương hiệu.
  plan: {
    Icon: ListChecks,
    frame: 'border-brand-600/40 bg-brand-50 dark:border-teal-700/60 dark:bg-teal-950/30',
    accent: 'text-brand-700 dark:text-teal-300',
  },
}

/**
 * Một khuôn duy nhất cho MỌI điểm dừng chờ quyết định.
 *
 * Ba loại chờ đều mang status `WAITING_APPROVAL` ở backend và chỉ khác nhau ở
 * discriminator. Nếu mỗi loại tự vẽ một kiểu thì sớm muộn chúng trôi dạt và
 * người dùng không còn đọc được sự khác biệt. Gom vào một component buộc ba
 * loại luôn khác nhau theo đúng những trục đã định: icon, khung màu, và — quan
 * trọng nhất — CÓ hay KHÔNG có nút.
 *
 * `provider` cố ý không nhận `actions`: chờ người khác duyệt thì không có gì
 * để bấm, và một khung trông-như-bấm-được sẽ khiến người dùng ngồi đợi một
 * cái nút không bao giờ xuất hiện.
 */
export function RiskGate({ kind, title, headline, hint, actions, children }: Props) {
  const view = GATE_VIEW[kind]

  return (
    <section
      className={`rounded-2xl border p-4 ${view.frame}`}
      aria-label={title}
    >
      <div className="flex items-start gap-3">
        <view.Icon className={`mt-0.5 h-5 w-5 shrink-0 ${view.accent}`} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className={`text-sm font-semibold ${view.accent}`}>{title}</p>
          {headline && (
            <p className="mt-0.5 text-lg font-semibold tabular-nums text-gray-900 dark:text-gray-100">
              {headline}
            </p>
          )}
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">{hint}</p>
          {children}
          {actions && <div className="mt-3 flex flex-wrap gap-2">{actions}</div>}
        </div>
      </div>
    </section>
  )
}
