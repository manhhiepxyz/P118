import { Handle, Position, type NodeProps } from '@xyflow/react'

import type { JourneyStep } from '../../lib/journeyMock'
import { STEP_STATE } from './stepState'

export type JourneyNodeData = { step: JourneyStep; selected: boolean }

/**
 * Một chặng của hành trình.
 *
 * Cấu trúc: dải trạng thái 2px bên trái · thân thẻ · hàng siêu dữ liệu. Dải
 * là thứ mang trạng thái — mắt quét dọc canvas đọc được thứ tự ưu tiên trước
 * khi đọc bất kỳ chữ nào, và nó vẫn hoạt động khi in đen trắng vì mỗi trạng
 * thái có DẤU riêng (đầy · rỗng · nét đứt) chứ không chỉ có màu.
 *
 * Không dùng quầng sáng làm nền tảng. Chặng đang chạy nổi lên bằng: bề mặt
 * cao hơn một tầng, viền đậm hơn, và một vệt quét chạy trong dải 2px. Tắt hết
 * hiệu ứng thì thứ bậc vẫn còn.
 */
export function JourneyNode({ data }: NodeProps) {
  const { step, selected } = data as unknown as JourneyNodeData
  const view = STEP_STATE[step.state]
  const quiet = view.presence === 'quiet' && !selected
  const focus = view.presence === 'focus'

  return (
    <div
      style={{ color: view.token, ['--edge-scan' as string]: 'currentColor' }}
      className={[
        'group relative w-[268px] overflow-hidden rounded-[var(--r-md)] border pl-[13px] pr-3.5 py-3',
        'transition-[background-color,border-color,box-shadow,opacity,transform] duration-[var(--t-node)] ease-[var(--ease)]',
        focus || selected
          ? 'border-[var(--border-strong)] bg-[var(--surface-overlay)] shadow-[inset_0_1px_0_var(--edge-light),var(--shadow-2)]'
          : 'border-[var(--border-subtle)] bg-[var(--surface-raised)] shadow-[inset_0_1px_0_var(--edge-light),var(--shadow-1)]',
        quiet ? 'opacity-[0.66] hover:opacity-100' : '',
        selected ? '-translate-y-px' : 'hover:-translate-y-px',
      ].join(' ')}
    >
      {/* Dải trạng thái. `mark` quyết định hình dạng, `token` quyết định màu. */}
      <span
        aria-hidden
        className={`absolute inset-y-0 left-0 w-[2px] overflow-hidden ${view.scan ? 'rail-scan' : ''}`}
        style={{
          backgroundColor:
            view.mark === 'hollow' ? 'color-mix(in srgb, currentColor 38%, transparent)' : 'currentColor',
          backgroundImage:
            view.mark === 'dashed'
              ? 'repeating-linear-gradient(180deg, currentColor 0 4px, transparent 4px 9px)'
              : undefined,
          opacity: view.mark === 'dashed' ? 0.45 : 1,
        }}
      />

      {/* Vòng chọn: đường 1px chính xác, không phải quầng sáng. */}
      {selected && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-[var(--r-md)]"
          style={{ boxShadow: 'inset 0 0 0 1.5px var(--selection)' }}
        />
      )}

      <Handle type="target" position={Position.Left} />

      <p className="text-[14.5px] font-semibold leading-[1.32] tracking-[-0.012em] text-[var(--text-primary)]">
        {step.title}
      </p>

      {!quiet && step.summary && (
        <p className="mt-1.5 line-clamp-2 text-[12px] leading-[1.5] text-[var(--text-secondary)]">
          {step.summary}
        </p>
      )}

      {/* Kết quả đã được XÁC NHẬN — chỉ hiện khi việc xong hẳn.
          Đây là phần thưởng của cả hành trình: tài xế, biển số, giờ đón, người
          đón tiếp. Bắt người dùng bấm vào node mới thấy là giấu đúng thứ họ
          chờ đợi. Nhãn do backend đặt (`details`), giao diện không tự dịch.
          Bốn dòng là trần: node phải còn đọc được khi zoom xa. */}
      {step.state === 'success' && step.details.length > 0 && (
        <dl className="mt-2.5 space-y-1 border-t border-[var(--border-subtle)] pt-2">
          {step.details.slice(0, 4).map((detail) => (
            <div key={detail.label} className="flex items-baseline gap-2 text-[11px] leading-[1.45]">
              <dt className="shrink-0 text-[var(--text-muted)]">{detail.label}</dt>
              <dd className="min-w-0 flex-1 truncate text-right font-medium text-[var(--text-primary)]">
                {detail.value}
              </dd>
            </div>
          ))}
          {step.details.length > 4 && (
            <p className="pt-0.5 text-[10.5px] text-[var(--text-muted)]">
              +{step.details.length - 4} thông tin nữa — bấm để xem
            </p>
          )}
        </dl>
      )}

      {/* Hàng siêu dữ liệu: trạng thái trái, giờ phải, phân cách bằng khoảng
          trống và cỡ chữ — không thêm đường kẻ. */}
      <div className="mt-2.5 flex items-center gap-1.5">
        <view.Icon
          className={`h-3 w-3 shrink-0 ${view.spin ? 'animate-spin' : ''}`}
          strokeWidth={2.4}
          aria-hidden
        />
        <span className="text-[10px] font-semibold uppercase tracking-[0.1em]">{view.label}</span>
        {step.timestamp && (
          <span className="ml-auto font-mono text-[10px] tabular-nums text-[var(--text-muted)]">
            {step.timestamp}
          </span>
        )}
      </div>

      {step.state === 'waiting_user' && step.actions.length > 0 && (
        <p className="mt-2.5 border-t border-[var(--border-subtle)] pt-2 text-[11px] font-semibold">
          Cần bạn xác nhận →
        </p>
      )}

      <Handle type="source" position={Position.Right} />
    </div>
  )
}
