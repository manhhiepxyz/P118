import type { NodeProps } from '@xyflow/react'

/**
 * Dải nền của một làn ngữ nghĩa (THAM QUAN · DI CHUYỂN · THANH TOÁN).
 *
 * Là node của React Flow chứ không phải phần tử phủ bên ngoài, để nó pan/zoom
 * cùng canvas. `pointer-events-none` để không nuốt cú bấm dành cho chặng nằm
 * trên nó.
 */
export function LaneNode({ data }: NodeProps) {
  const { title, height } = data as unknown as { title: string; height: number }

  return (
    <div className="pointer-events-none w-[770px]" style={{ height }}>
      {/* Làn là GỢI Ý phân nhóm, không phải cái hộp: chỉ một đường kẻ trên và
          một nhãn. Vẽ thành khối có nền thì nó tranh chú ý với chính các chặng
          nằm bên trong. */}
      <div className="flex items-center gap-3 border-t border-[var(--border-subtle)] pt-2">
        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.24em] text-[var(--text-secondary)]">
          {title}
        </span>
        <span className="h-px flex-1 bg-[var(--border-subtle)] opacity-50" aria-hidden />
      </div>
    </div>
  )
}
