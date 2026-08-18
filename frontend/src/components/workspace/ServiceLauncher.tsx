import { useEffect, useState } from 'react'
import {
  Building2,
  CarFront,
  Check,
  ChevronDown,
  Lock,
  MessagesSquare,
  Pencil,
  Plus,
  Truck,
  Wrench,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { getCapabilities } from '../../lib/agentApi'
import {
  SERVICE_FIELDS,
  SHARED_FIELDS,
  missingFields,
  summarise,
  type FormValues,
} from '../../lib/serviceForms'
import type { Capability } from '../../lib/types'
import { InlineServiceForm } from './InlineServiceForm'

export const IDENTITY: Record<string, { Icon: LucideIcon; group: string }> = {
  'Đặt lịch tham quan dự án': { Icon: Building2, group: 'Bất động sản' },
  'Đăng ký quan tâm / nhận tư vấn': { Icon: MessagesSquare, group: 'Bất động sản' },
  'Đăng ký phương tiện và chỗ đỗ xe': { Icon: CarFront, group: 'Xe cộ' },
  'Báo bảo trì / sửa chữa': { Icon: Wrench, group: 'Cư dân' },
  'Đặt lịch chuyển nhà': { Icon: Truck, group: 'Cư dân' },
}
const FALLBACK_IDENTITY = { Icon: Plus, group: 'Khác' }

/** Năng lực backend có rao nhưng giao diện hỏi ở chỗ khác. */
const HIDDEN = new Set(['Đặt xe đưa đón tham quan'])

/* Danh sách dự phòng khi `/capabilities` chưa trả lời — phải CHÉP ĐÚNG
   `_CAPABILITY_CATALOGUE` trong `src/api/routes.py`.

   "Tư vấn tài chính, vay mua nhà" đã bị bỏ: nó không có trong catalogue và
   không có tool nào trong `TOOL_CONTRACTS` thực hiện được. Mời người dùng một
   dịch vụ không tồn tại là hứa một việc chắc chắn thất bại.

   TODO(backend): bỏ hẳn danh sách này khi nguyên mẫu chạy cùng stack thật. */
const FALLBACK: Capability[] = [
  { name: 'Đặt lịch tham quan dự án', description: 'Chọn dự án, ngày và giờ muốn tham quan.', requires_resident: false, available: true, blocked_reason: null },
  { name: 'Đăng ký quan tâm / nhận tư vấn', description: 'Gửi nhu cầu để bộ phận tư vấn liên hệ.', requires_resident: false, available: true, blocked_reason: null },
  { name: 'Đăng ký phương tiện và chỗ đỗ xe', description: 'Đăng ký xe và giữ chỗ đỗ ở Khu A hoặc Khu B.', requires_resident: true, available: false, blocked_reason: 'Cần xác minh căn hộ trước.' },
  { name: 'Báo bảo trì / sửa chữa', description: 'Tạo yêu cầu và hẹn lịch kỹ thuật viên.', requires_resident: true, available: false, blocked_reason: 'Cần xác minh căn hộ trước.' },
  { name: 'Đặt lịch chuyển nhà', description: 'Đăng ký thời gian, thang máy và hỗ trợ vận chuyển.', requires_resident: true, available: false, blocked_reason: 'Cần xác minh căn hộ trước.' },
]

interface Props {
  selected: string[]
  onToggle: (name: string) => void
  values: Record<string, FormValues>
  shared: FormValues
  onField: (service: string, key: string, value: string, isShared: boolean) => void
  invalid: Record<string, string[]>
  leaving: boolean
}

/**
 * MODE A — vùng năng lực, bề mặt chính trước khi chạy.
 *
 * Chọn một năng lực là BUNG NGAY các ô cần điền, không đợi agent hỏi lại qua
 * hội thoại. Những field này có tập giá trị hữu hạn và biết trước; hỏi bằng
 * chat biến thao tác 5 giây thành một cuộc đối thoại nhiều lượt, mỗi lượt tốn
 * một lần gọi model.
 *
 * Điền đủ thì dòng tự gập lại thành một dòng tóm tắt — danh sách không phình
 * ra vô hạn khi chọn nhiều việc, và người dùng vẫn thấy toàn cảnh.
 */
export function ServiceLauncher({
  selected,
  onToggle,
  values,
  shared,
  onField,
  invalid,
  leaving,
}: Props) {
  const [items, setItems] = useState<Capability[]>(FALLBACK)
  /** Dòng đang mở form. Điền đủ thì tự đóng, bấm "Sửa" thì mở lại. */
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getCapabilities()
      // Backend vẫn rao "Đặt xe đưa đón tham quan" như một mục riêng, nhưng nó
      // cần `viewing_id` — thứ chỉ có sau khi đã đặt lịch. Ở đây xe đón được
      // hỏi ngay trong dịch vụ tham quan, nên bày thêm một mục nữa là mời
      // người dùng đi vào ngõ cụt.
      //
      // TODO(backend): gỡ khỏi `_CAPABILITY_CATALOGUE` để hai bên không lệch.
      .then((data) => alive && data.length > 0 && setItems(data.filter((item) => !HIDDEN.has(item.name))))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const anyShared = selected.some((name) =>
    (SERVICE_FIELDS[name] ?? []).some((field) => field.shared),
  )

  /** Key dùng chung đang bị gắn cờ thiếu, gom từ mọi dịch vụ đã chọn. */
  const sharedInvalid = new Set(
    Object.values(invalid)
      .flat()
      .filter((key) => SHARED_FIELDS.some((field) => field.key === key)),
  )

  return (
    <div className={`h-full overflow-y-auto ${leaving ? 'shift-out' : 'rise'}`}>
      <div className="mx-auto w-full max-w-[1000px] px-12 pb-8 pt-12">
        <p className="font-mono text-[12px] font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">
          Trợ lý dịch vụ cư dân
        </p>
        <h1 className="mt-4 text-[38px] font-semibold leading-[1.12] tracking-[-0.03em] text-[var(--text-primary)]">
          P-118 làm được gì cho bạn?
        </h1>
        <p className="mt-3.5 max-w-2xl text-[16px] leading-[1.6] text-[var(--text-secondary)]">
          Chọn một hoặc nhiều dịch vụ rồi điền thông tin ngay tại chỗ. Hoặc cứ
          nói bằng lời ở ô bên dưới — P-118 tự hiểu phần còn lại.
        </p>

        {/* Thông tin dùng chung — chỉ hiện khi có việc thật sự cần nó.

            Khối này PHẢI biết về `invalid`. Trước đây nó không biết: bấm Thực
            hiện mà thiếu Dự án/Ngày thì `execute()` chặn và gắn cờ lỗi, nhưng
            cờ ấy chỉ được vẽ trong form của từng dịch vụ — nơi field dùng
            chung đã bị lọc ra. Kết quả là bấm nút và KHÔNG có gì xảy ra: không
            chạy, không báo lỗi, không chỗ nào đỏ. */}
        {anyShared && (
          <section className="rise mt-10">
            <h2 className="font-mono text-[12px] font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">
              Thông tin hành trình
            </h2>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              {SHARED_FIELDS.map((field) => {
                const bad = sharedInvalid.has(field.key)
                const border = bad ? 'border-[var(--danger)]' : 'border-[var(--border-subtle)]'
                return (
                  <div key={field.key}>
                    <label
                      htmlFor={`shared-${field.key}`}
                      className="block text-[13.5px] font-medium text-[var(--text-secondary)]"
                    >
                      {field.label}
                    </label>
                    {field.kind === 'select' ? (
                      <select
                        id={`shared-${field.key}`}
                        value={shared[field.key] ?? ''}
                        onChange={(event) => onField('', field.key, event.target.value, true)}
                        aria-invalid={bad}
                        aria-describedby={bad ? `shared-${field.key}-err` : undefined}
                        className={`mt-2 h-12 w-full cursor-pointer rounded-[var(--r-sm)] border bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors focus:border-[var(--selection)] ${border}`}
                      >
                        <option value="">Chọn…</option>
                        {field.options?.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        id={`shared-${field.key}`}
                        type="date"
                        value={shared[field.key] ?? ''}
                        onChange={(event) => onField('', field.key, event.target.value, true)}
                        aria-invalid={bad}
                        aria-describedby={bad ? `shared-${field.key}-err` : undefined}
                        className={`mt-2 h-12 w-full rounded-[var(--r-sm)] border bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors focus:border-[var(--selection)] ${border}`}
                      />
                    )}
                    {bad ? (
                      <p id={`shared-${field.key}-err`} className="mt-1.5 text-[12.5px] text-[var(--danger)]">
                        Chưa chọn {field.label.toLowerCase()}.
                      </p>
                    ) : field.hint ? (
                      <p className="mt-1.5 text-[12.5px] text-[var(--text-muted)]">{field.hint}</p>
                    ) : null}
                  </div>
                )
              })}
            </div>
          </section>
        )}

        <div className="mt-11 flex items-baseline justify-between">
          <h2 className="font-mono text-[12px] font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">
            Dịch vụ
          </h2>
          <p className="font-mono text-[12px] tabular-nums text-[var(--text-muted)]">
            {selected.length > 0
              ? `${selected.length} đã chọn`
              : `${items.filter((item) => item.available).length}/${items.length} khả dụng`}
          </p>
        </div>

        <ul className="seq mt-4 border-t border-[var(--border-subtle)]">
          {items.map((item, index) => {
            const locked = !item.available
            const identity = IDENTITY[item.name] ?? FALLBACK_IDENTITY
            const isSelected = selected.includes(item.name)
            const fields = SERVICE_FIELDS[item.name] ?? []
            const missing = missingFields(item.name, values[item.name] ?? {}, shared)
            const complete = isSelected && fields.length > 0 && missing.length === 0
            const expanded = isSelected && open === item.name && !complete
            const showForm = isSelected && (open === item.name || !complete)

            return (
              <li key={item.name} className="border-b border-[var(--border-subtle)]">
                <button
                  type="button"
                  disabled={locked}
                  onClick={() => {
                    if (isSelected) {
                      onToggle(item.name)
                      setOpen(null)
                    } else {
                      onToggle(item.name)
                      setOpen(item.name)
                    }
                  }}
                  onFocus={() => undefined}
                  aria-pressed={isSelected}
                  aria-expanded={showForm}
                  /* min-h 76px: hàng đủ cao để quét thoải mái ở khoảng cách
                     ngồi bình thường, không phải nghiêng người vào màn hình. */
                  className={`group relative grid min-h-[76px] w-full grid-cols-[30px_44px_1fr_auto] items-center gap-5 py-4 pl-4 pr-4 text-left
                    transition-[background-color,padding] duration-[180ms] ease-[var(--ease)] ${
                      locked
                        ? 'cursor-not-allowed opacity-70'
                        : 'cursor-pointer hover:bg-[var(--surface-raised)] hover:pl-6'
                    } ${isSelected ? 'bg-[var(--surface-raised)]' : ''}`}
                >
                  <span
                    aria-hidden
                    className="absolute inset-y-0 left-0 w-[3px] origin-center transition-transform duration-[180ms] ease-[var(--ease)]"
                    style={{
                      backgroundColor: 'var(--agent)',
                      transform: isSelected ? 'scaleY(1)' : 'scaleY(0)',
                    }}
                  />

                  <span className="font-mono text-[13px] tabular-nums text-[var(--text-muted)]">
                    {String(index + 1).padStart(2, '0')}
                  </span>

                  <span
                    aria-hidden
                    className={`flex h-11 w-11 items-center justify-center rounded-[var(--r-sm)] border transition-all duration-[180ms] ease-[var(--ease)] ${
                      isSelected
                        ? 'border-transparent'
                        : locked
                          ? 'border-[var(--border-subtle)] text-[var(--text-muted)]'
                          : 'border-[var(--border-subtle)] text-[var(--text-secondary)] group-hover:-translate-y-px group-hover:border-[var(--agent)] group-hover:text-[var(--agent)]'
                    }`}
                    style={isSelected ? { backgroundColor: 'var(--agent)', color: 'var(--surface-base)' } : undefined}
                  >
                    {isSelected ? (
                      <Check className="h-5 w-5" strokeWidth={2.6} />
                    ) : (
                      <identity.Icon className="h-5 w-5" strokeWidth={1.8} />
                    )}
                  </span>

                  <span className="min-w-0">
                    <span className="block text-[16px] font-semibold leading-[1.35] tracking-[-0.01em] text-[var(--text-primary)]">
                      {item.name}
                    </span>
                    {/* Đã đủ thông tin → thay mô tả bằng chính lựa chọn. */}
                    {complete ? (
                      <span className="mt-1 block text-[14px] leading-[1.5] text-[var(--agent)]">
                        {summarise(item.name, values[item.name] ?? {}, shared)}
                      </span>
                    ) : (
                      <span className="mt-1 block text-[14px] leading-[1.5] text-[var(--text-secondary)]">
                        {item.description}
                      </span>
                    )}
                    {locked && item.blocked_reason && (
                      <span
                        className="mt-1.5 inline-flex items-center gap-1.5 text-[13px] font-medium"
                        style={{ color: 'var(--waiting-user)' }}
                      >
                        <Lock className="h-3.5 w-3.5" aria-hidden />
                        {item.blocked_reason}
                      </span>
                    )}
                  </span>

                  <span className="flex items-center gap-4">
                    {complete ? (
                      <span
                        onClick={(event) => {
                          event.stopPropagation()
                          setOpen(item.name)
                        }}
                        className="inline-flex items-center gap-1.5 rounded-[var(--r-xs)] px-2 py-1 text-[13px] font-semibold text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                      >
                        <Pencil className="h-3.5 w-3.5" strokeWidth={2.2} aria-hidden />
                        Sửa
                      </span>
                    ) : (
                      <>
                        <span className="hidden font-mono text-[12px] uppercase tracking-[0.12em] text-[var(--text-muted)] transition-opacity duration-[180ms] group-hover:opacity-0 lg:inline">
                          {identity.group}
                        </span>
                        {!locked && (
                          <span className="pointer-events-none absolute right-4 hidden items-center gap-1.5 text-[13px] font-semibold text-[var(--agent)] opacity-0 transition-opacity duration-[180ms] group-hover:opacity-100 lg:flex">
                            {isSelected ? 'Bỏ chọn' : 'Chọn'}
                            <ChevronDown
                              className={`h-4 w-4 transition-transform duration-[180ms] ${expanded ? 'rotate-180' : ''}`}
                              strokeWidth={2.4}
                              aria-hidden
                            />
                          </span>
                        )}
                      </>
                    )}
                  </span>
                </button>

                {/* Form bung ra TRONG dòng — không phải thẻ riêng. */}
                {showForm && fields.length > 0 && (
                  <div className="rise bg-[var(--surface-raised)] px-4 pb-6 pl-[95px] pr-6">
                    <InlineServiceForm
                      /* Bỏ field DÙNG CHUNG khỏi form riêng: chúng đã có ở
                         khối "Thông tin hành trình" phía trên. Hiện lại lần
                         hai vừa thừa vừa gây nghi ngờ — người dùng không biết
                         hai ô đó có phải cùng một thứ không. Validation vẫn
                         xét chúng qua `missingFields`. */
                      fields={fields.filter((field) => !field.shared)}
                      values={values[item.name] ?? {}}
                      shared={shared}
                      onChange={(key, value, isShared) => onField(item.name, key, value, isShared)}
                      invalid={invalid[item.name] ?? []}
                    />
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
