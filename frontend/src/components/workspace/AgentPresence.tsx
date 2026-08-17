import { STEP_STATE } from './stepState'
import { JOURNEY_STEPS } from '../../lib/journeyMock'

/**
 * Sự hiện diện của P-118 trong chính không gian làm việc.
 *
 * Không phải logo, không phải linh vật: là một chỉ báo trạng thái hệ thống.
 * Chấm sáng có vòng sóng lan khi agent đang điều phối, và tắt hẳn khi rảnh —
 * chuyển động ở đây mang thông tin, nên nó không được chạy vô cớ.
 *
 * Câu trạng thái nói ĐANG LÀM GÌ ("đang điều phối 2 dịch vụ") chứ không nói
 * trạng thái kỹ thuật. Đây là chỗ người dùng liếc để biết hệ thống còn sống.
 */
interface Props {
  /** Rảnh = chưa có hành trình nào chạy. */
  idle?: boolean
}

export function AgentPresence({ idle = false }: Props) {
  const active = JOURNEY_STEPS.filter(
    (step) => STEP_STATE[step.state].presence === 'focus',
  ).length

  const busy = !idle && active > 0
  const token = busy ? 'var(--running)' : 'var(--text-muted)'

  return (
    <div
      className="flex items-center gap-2.5 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] py-1 pl-2.5 pr-3"
      style={{ color: token }}
    >
      <span className="relative flex h-1.5 w-1.5 shrink-0">
        {busy && (
          <span
            className="halo absolute inline-flex h-full w-full rounded-full bg-current"
            aria-hidden
          />
        )}
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      </span>

      <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.16em] text-[var(--text-primary)]">
        P-118
      </span>
      <span className="text-[11px] font-medium" aria-live="polite">
        {busy ? `Đang điều phối ${active} dịch vụ` : 'Sẵn sàng'}
      </span>
    </div>
  )
}
