import { useState } from 'react'
import { ChevronDown } from 'lucide-react'

interface DetailItem {
  label: string
  value: string
}

interface Props {
  details: DetailItem[]
  /** Mở sẵn khi hành trình đã xong — lúc đó chi tiết CHÍNH LÀ kết quả. */
  defaultOpen?: boolean
  /** Nhãn cho screen reader biết chi tiết này thuộc bước nào. */
  ownerLabel: string
}

/**
 * Gập/mở phần chi tiết do backend trả về.
 *
 * Vì sao phải gập: số dòng chênh nhau rất xa giữa các nghiệp vụ — `pay_fee`
 * có 2 dòng, `schedule_property_viewing` có 7, `book_shuttle` có 8. Một yêu
 * cầu 3 việc mở hết cùng lúc đẩy thẻ cao vài trăm pixel và dồn mọi thứ phía
 * trên ra khỏi màn hình. Mở theo yêu cầu giữ mật độ đọc được mà không giấu
 * mất dữ liệu.
 *
 * Nhãn và giá trị đều do BACKEND đặt. Component này không biết — và không
 * được biết — dòng nào thuộc nghiệp vụ nào.
 */
export function DetailDisclosure({ details, defaultOpen = false, ownerLabel }: Props) {
  const [open, setOpen] = useState(defaultOpen)
  if (details.length === 0) return null

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 rounded-lg px-1.5 py-1 text-xs font-medium text-brand-600 transition hover:bg-brand-50 dark:text-teal-400 dark:hover:bg-teal-950/40"
      >
        <ChevronDown
          className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden
        />
        {open ? 'Thu gọn' : `Xem chi tiết (${details.length})`}
        <span className="sr-only"> — {ownerLabel}</span>
      </button>

      {open && (
        <dl className="mt-2 grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5 rounded-xl bg-gray-50 px-3 py-2.5 text-xs dark:bg-gray-100/5">
          {details.map((item, index) => (
            <div key={`${item.label}-${index}`} className="contents">
              <dt className="text-gray-500 dark:text-gray-400">{item.label}</dt>
              {/* `break-words` vì giá trị có thể là mã dài hoặc tên đầy đủ. */}
              <dd className="break-words font-medium text-gray-900 dark:text-gray-100">
                {item.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
