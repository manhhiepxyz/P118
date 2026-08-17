import { Info } from 'lucide-react'

import type { JourneyStep } from '../../lib/journeyMock'
import { STEP_STATE } from './stepState'

interface Props {
  step: JourneyStep | null
}

/**
 * Bảng ngữ cảnh của chặng đang chọn.
 *
 * Không phải sidebar biểu mẫu: nó là một tấm nổi có ánh sáng riêng, và khối
 * trạng thái ở đầu mang đúng sắc phát quang của node vừa chọn — mắt nối được
 * "chỗ tôi vừa bấm" với "cái đang hiện ở đây" mà không cần mũi tên chỉ dẫn.
 *
 * `key={step.id}` để đổi chặng thì React dựng lại nhánh, hoạt ảnh vào chạy
 * lại, và nội dung hoà tan thay vì thay phắt.
 */
export function InspectorPanel({ step }: Props) {
  if (!step) return null
  const view = STEP_STATE[step.state]

  return (
    <div key={step.id} className="rise flex h-full flex-col overflow-hidden" style={{ color: view.token }}>
      {/* Khối trạng thái: nền nhuốm sắc của chặng, viền dưới phát sáng. */}
      {/* Đầu panel: một dải trạng thái 2px ở mép trái nối tiếp thị giác với
          dải trên node vừa chọn, cộng nền nhuốm rất nhạt. Không gradient phát
          sáng — chiều sâu đến từ tầng bề mặt và đường kẻ. */}
      <header
        className="relative shrink-0 border-b border-[var(--border-subtle)] px-6 pb-5 pt-5"
        style={{ backgroundColor: 'color-mix(in srgb, currentColor 6%, transparent)' }}
      >
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[2px]"
          style={{ backgroundColor: 'currentColor' }}
        />
        <span
          className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.12em]"
          style={{
            backgroundColor: 'color-mix(in srgb, currentColor 15%, transparent)',
            boxShadow: 'inset 0 0 0 1px color-mix(in srgb, currentColor 30%, transparent)',
          }}
        >
          <view.Icon className={`h-3 w-3 ${view.spin ? 'animate-spin' : ''}`} aria-hidden />
          {view.label}
        </span>

        <h2 className="mt-3 text-[20px] font-semibold leading-[1.22] tracking-[-0.02em] text-[var(--text-primary)]">
          {step.title}
        </h2>
        {step.timestamp && (
          <p className="mt-1.5 font-mono text-[11px] tabular-nums text-[var(--text-muted)]">
            Cập nhật {step.timestamp}
          </p>
        )}
      </header>

      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 py-5">
        <p className="text-[13.5px] leading-[1.65] text-[var(--text-secondary)]">{step.summary}</p>

        {step.waitingOn && (
          <div
            className="rounded-[var(--r-md)] px-4 py-3.5"
            style={{
              backgroundColor: 'color-mix(in srgb, currentColor 10%, transparent)',
              boxShadow: 'inset 0 0 0 1px color-mix(in srgb, currentColor 22%, transparent)',
            }}
          >
            <p className="flex items-start gap-2.5 text-[13px] leading-[1.55]">
              <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <span className="text-[var(--text-primary)]">{step.waitingOn}</span>
            </p>
          </div>
        )}

        {step.details.length > 0 && (
          <section>
            <div className="flex items-baseline gap-3">
              <h3 className="text-[10px] font-bold uppercase tracking-[0.15em] text-[var(--text-muted)]">
                Thông tin
              </h3>
              <span className="h-px flex-1 bg-[var(--border-subtle)]" aria-hidden />
            </div>
            <dl className="mt-4 space-y-3.5">
              {step.details.map((detail) => (
                <div key={detail.label}>
                  <dt className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-[var(--text-muted)]">
                    {detail.label}
                  </dt>
                  <dd className="mt-1 break-words text-[13.5px] font-medium leading-[1.45] text-[var(--text-primary)]">
                    {detail.value}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}
      </div>

      {step.actions.length > 0 && (
        <footer className="shrink-0 border-t border-[var(--border-subtle)] px-6 py-4">
          <div className="flex flex-col gap-2">
            {step.actions.map((action, index) => {
              const isPrimary = action.tone === 'primary' || (index === 0 && action.tone !== 'danger')
              return (
                <button
                  key={action.label}
                  type="button"
                  className="press min-h-11 w-full cursor-pointer rounded-[var(--r-sm)] px-4 text-[13.5px] font-semibold transition-colors"
                  /* Nút chính tô ĐẶC bằng màu trạng thái. Bản trước dùng
                     `color-mix … 22%` nên trên nền tối nó ra xám và đọc như
                     nút phụ — người dùng không thấy đâu là việc cần làm. */
                  /* Dùng `view.token` chứ KHÔNG dùng `currentColor`: đặt
                     `color` trên cùng phần tử sẽ khiến `backgroundColor:
                     currentColor` lấy đúng màu vừa đặt — nút biến mất vào nền.
                     Đã xảy ra đúng như vậy. */
                  style={
                    isPrimary
                      ? { backgroundColor: view.token, color: 'var(--surface-base)' }
                      : {
                          boxShadow: 'inset 0 0 0 1px var(--border-strong)',
                          color: 'var(--text-secondary)',
                        }
                  }
                >
                  {action.label}
                </button>
              )
            })}
          </div>
        </footer>
      )}
    </div>
  )
}
