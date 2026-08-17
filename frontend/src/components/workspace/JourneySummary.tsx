import { STEP_STATE } from './stepState'
import type { JourneyStep } from '../../lib/journeyMock'

/**
 * Trạng thái mặc định của bảng ngữ cảnh — khi chưa chọn chặng nào.
 *
 * Panel trống là khoảng lãng phí ngay cạnh nội dung chính, và bắt người dùng
 * phải bấm gì đó mới thấy thông tin. Ở đây trả lời câu hỏi bao quát nhất:
 * hành trình này đang đi tới đâu, và có gì đang chờ mình.
 */
/**
 * `hideWaiting` — tắt khối "việc chờ bạn" khi `PendingCard` đang hiện ngay
 * phía trên. Hai khối cùng nói "Thanh toán đang chờ bạn", cách nhau vài chục
 * pixel, và chỉ MỘT trong hai bấm được: người dùng phải tự đoán cái nào là
 * thật. Nhắc lại một việc không làm nó rõ hơn.
 */
export function JourneySummary({
  steps,
  title,
  hideWaiting = false,
}: {
  steps: JourneyStep[]
  title: string
  hideWaiting?: boolean
}) {
  const counts = steps.reduce<Record<string, number>>((acc, step) => {
    acc[step.state] = (acc[step.state] ?? 0) + 1
    return acc
  }, {})

  const needsYou = steps.filter((step) => step.state === 'waiting_user')
  const done = counts.success ?? 0
  // Kế hoạch rỗng (đang lập) thì 0/0 — chia cho 0 ra NaN và thanh tiến độ biến mất.
  const progress = steps.length === 0 ? 0 : Math.round((done / steps.length) * 100)

  return (
    <div className="rise h-full overflow-y-auto px-6 py-6">
      <span className="text-[10px] font-bold uppercase tracking-[0.15em] text-[var(--text-muted)]">
        Hành trình
      </span>
      <h2 className="mt-2.5 text-[20px] font-semibold leading-[1.22] tracking-[-0.02em] text-[var(--text-primary)]">
        {title}
      </h2>

      {/* Thanh tiến độ mảnh, phát sáng theo màu thương hiệu. */}
      <div className="mt-5">
        <div className="flex items-baseline justify-between">
          <span className="text-[11px] font-medium text-[var(--text-secondary)]">Tiến độ</span>
          <span className="font-mono text-[12px] font-semibold tabular-nums text-[var(--text-primary)]">
            {done}/{steps.length}
          </span>
        </div>
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-[rgb(255_255_255/0.07)]">
          <div
            className="h-full rounded-full transition-[width] duration-500 ease-[var(--ease)]"
            style={{ width: `${progress}%`, backgroundColor: 'var(--agent)' }}
          />
        </div>
      </div>

      {needsYou.length > 0 && !hideWaiting && (
        <div
          className="mt-6 rounded-[var(--r-md)] px-4 py-3.5"
          style={{
            color: 'var(--waiting-user)',
            backgroundColor: 'color-mix(in srgb, currentColor 11%, transparent)',
            boxShadow: 'inset 0 0 0 1px color-mix(in srgb, currentColor 26%, transparent)',
          }}
        >
          <p className="text-[12.5px] font-bold uppercase tracking-[0.08em]">
            {needsYou.length} việc chờ bạn
          </p>
          <ul className="mt-2 space-y-1">
            {needsYou.map((step) => (
              <li key={step.id} className="text-[13px] text-[var(--text-primary)]">
                {step.title}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-7 flex items-baseline gap-3">
        <h3 className="text-[10px] font-bold uppercase tracking-[0.15em] text-[var(--text-muted)]">
          Tình hình
        </h3>
        <span className="h-px flex-1 bg-[var(--border-subtle)]" aria-hidden />
      </div>

      <dl className="mt-4 space-y-2.5">
        {Object.entries(counts).map(([state, count]) => {
          const view = STEP_STATE[state as keyof typeof STEP_STATE]
          if (!view) return null
          return (
            <div key={state} className="flex items-center gap-2.5" style={{ color: view.token }}>
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: 'currentColor' }}
                aria-hidden
              />
              <dt className="flex-1 text-[13px] text-[var(--text-secondary)]">{view.label}</dt>
              <dd className="font-mono text-[13px] font-semibold tabular-nums text-[var(--text-primary)]">
                {count}
              </dd>
            </div>
          )
        })}
      </dl>

      <p className="mt-7 border-t border-[var(--border-subtle)] pt-5 text-[12.5px] leading-[1.6] text-[var(--text-muted)]">
        Chọn một chặng trên canvas để xem chi tiết và các việc bạn có thể làm.
      </p>
    </div>
  )
}
