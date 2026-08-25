import { useEffect, useState } from 'react'
import { ArrowRight, Building2, ShieldCheck } from 'lucide-react'

/**
 * Màn chuyển giao khi rời P-118 sang cổng của đơn vị xác thực.
 *
 * `/apartment-link` → `/verify` là một chuyển route trong cùng ứng dụng, nên
 * nó xảy ra tức thì. Người dùng bấm "Xác thực với đơn vị" và màn hình đổi ngay
 * — không có gì báo rằng họ vừa vượt qua một ranh giới tin cậy và sắp tải ảnh
 * sổ hồng lên một hệ thống khác. Cả kiến trúc lẫn phần chân trang đều nói đây
 * là bên thứ ba; chỉ có trải nghiệm là không.
 *
 * Màn này KHÔNG phải delay giả. Nó chờ đúng việc có thật: `/verify` phải nạp
 * hồ sơ hiện có trước khi biết nên hiện form hay hiện trạng thái. Trước đây
 * việc nạp đó hiện ra dưới dạng một khối xám nhấp nháy sau khi đã chuyển
 * trang; giờ nó nằm ở đây, có nội dung giải thích chuyện gì đang xảy ra.
 *
 * `MIN_MS` là sàn hiển thị, không phải trần. Nạp xong trong 80ms mà đổi màn
 * ngay thì người dùng chỉ thấy một cú giật — đó chính là thứ cần sửa. Nạp lâu
 * hơn sàn thì màn này ở lại đúng bằng thời gian nạp, không cộng thêm.
 */
const MIN_MS = 900

export function usePortalHandoff(ready: boolean): boolean {
  const [floorPassed, setFloorPassed] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => setFloorPassed(true), MIN_MS)
    return () => window.clearTimeout(timer)
  }, [])

  return !(ready && floorPassed)
}

export function PortalHandoff() {
  return (
    <div
      className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center"
      // `status` chứ không phải `alert`: đây là tiến trình, không phải sự cố.
      // Trình đọc màn hình đọc nó mà không cắt ngang việc đang đọc dở.
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-4">
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-teal-700 text-white">
          <ShieldCheck className="h-6 w-6" aria-hidden />
        </span>
        {/* Ba chấm chạy từ trái sang phải: hướng chuyển động nói rõ đang đi
            TỪ P-118 SANG đơn vị xác thực, không phải ngược lại. */}
        <span className="flex items-center gap-1.5" aria-hidden>
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-400"
              style={{ animationDelay: `${i * 180}ms` }}
            />
          ))}
        </span>
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-950 text-white">
          <Building2 className="h-6 w-6" aria-hidden />
        </span>
      </div>

      <p className="mt-6 text-base font-semibold text-gray-900 dark:text-gray-100">
        Đang chuyển sang cổng của đơn vị xác thực
      </p>
      <p className="mt-2 max-w-[46ch] text-sm leading-relaxed text-gray-500">
        Việc đối chiếu giấy tờ chủ sở hữu do một đơn vị độc lập thực hiện, không phải P-118. Ảnh
        giấy tờ bạn tải lên được gửi thẳng cho họ.
      </p>
      <p className="mt-4 inline-flex items-center gap-1.5 text-xs text-gray-400">
        P-118
        <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        Cổng xác thực chủ sở hữu
      </p>
    </div>
  )
}
