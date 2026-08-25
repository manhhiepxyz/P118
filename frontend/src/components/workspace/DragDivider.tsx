import { useCallback, useEffect, useRef } from 'react'

/**
 * Thanh chia kéo được giữa canvas và hội thoại.
 *
 * VÌ SAO CÓ: ở chế độ hành trình, khung hội thoại không có ràng buộc chiều cao
 * nên nó nở ra theo số lượt và bị cắt ngang — `ConversationStream` có sẵn
 * `overflow-y-auto` nhưng không bao giờ cuộn vì cha nó không giới hạn gì.
 *
 * KÉO KHÔNG PHẢI CÁCH DUY NHẤT. WCAG 2.2 (dragging-alternative) đòi mọi thao
 * tác kéo phải có đường đi bằng một-lần-trỏ và bằng bàn phím. Ở đây:
 *
 *     kéo chuột/chạm    → đặt vị trí tự do
 *     ↑ ↓               → dịch từng nấc
 *     Home / End        → về mức nhỏ nhất / lớn nhất
 *     Enter             → đảo giữa hai mức đó
 *
 * Thiếu chúng thì người dùng bàn phím và người không kéo được chuột chính xác
 * bị kẹt với tỉ lệ mặc định, mà đó lại là những người cần chỉnh nhất.
 *
 * Vùng bắt tay dày hơn nét vẽ: nét là 1px cho gọn mắt, nhưng vùng bắt cao 16px
 * để không phải nhắm. Nét chỉ đậm lên khi trỏ vào hoặc đang focus — một đường
 * kẻ luôn đậm giữa màn hình đọc như một lỗi giao diện.
 */
export function DragDivider({
  value,
  onChange,
  min = 0.25,
  max = 0.75,
  step = 0.04,
  label = 'Kéo để đổi tỉ lệ giữa sơ đồ và hội thoại',
}: {
  /** Tỉ lệ chiều cao phần TRÊN, 0..1. */
  value: number
  onChange: (next: number) => void
  min?: number
  max?: number
  step?: number
  label?: string
}) {
  const keo = useRef<{ dau: number; caoCha: number; batDau: number } | null>(null)
  const than = useRef<HTMLDivElement>(null)

  const ganh = useCallback((v: number) => Math.min(max, Math.max(min, v)), [min, max])

  useEffect(() => {
    function diChuyen(e: PointerEvent) {
      const k = keo.current
      if (!k || k.caoCha <= 0) return
      onChange(ganh(k.batDau + (e.clientY - k.dau) / k.caoCha))
    }
    function nhaTay() {
      keo.current = null
      document.body.style.removeProperty('user-select')
      document.body.style.removeProperty('cursor')
    }
    window.addEventListener('pointermove', diChuyen)
    window.addEventListener('pointerup', nhaTay)
    window.addEventListener('pointercancel', nhaTay)
    return () => {
      window.removeEventListener('pointermove', diChuyen)
      window.removeEventListener('pointerup', nhaTay)
      window.removeEventListener('pointercancel', nhaTay)
    }
  }, [ganh, onChange])

  return (
    <div
      ref={than}
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      aria-valuenow={Math.round(value * 100)}
      aria-valuemin={Math.round(min * 100)}
      aria-valuemax={Math.round(max * 100)}
      tabIndex={0}
      onPointerDown={(e) => {
        const cha = than.current?.parentElement
        if (!cha) return
        keo.current = { dau: e.clientY, caoCha: cha.getBoundingClientRect().height, batDau: value }
        // Kéo qua canvas mà không khoá chọn chữ thì nửa trang bị bôi đen.
        document.body.style.setProperty('user-select', 'none')
        document.body.style.setProperty('cursor', 'row-resize')
      }}
      onKeyDown={(e) => {
        if (e.key === 'ArrowUp') { e.preventDefault(); onChange(ganh(value - step)) }
        else if (e.key === 'ArrowDown') { e.preventDefault(); onChange(ganh(value + step)) }
        else if (e.key === 'Home') { e.preventDefault(); onChange(min) }
        else if (e.key === 'End') { e.preventDefault(); onChange(max) }
        else if (e.key === 'Enter') { e.preventDefault(); onChange(value > (min + max) / 2 ? min : max) }
      }}
      className="group relative flex h-4 w-full shrink-0 cursor-row-resize items-center outline-none"
      style={{ touchAction: 'none' }}
    >
      <div className="h-px w-full bg-[var(--border-subtle)] transition-[height,background-color] duration-150 group-hover:h-0.5 group-hover:bg-[var(--agent)] group-focus-visible:h-0.5 group-focus-visible:bg-[var(--agent)]" />
    </div>
  )
}
