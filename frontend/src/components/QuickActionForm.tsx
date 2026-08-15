import { useEffect, useState } from 'react'

import { listProjects } from '../lib/agentApi'
import type { Capability } from '../lib/types'

interface Props {
  capabilities: Capability[]
  submitting: boolean
  onSubmit: (goal: string) => Promise<void>
  onCancel: () => void
}

type Values = Record<string, string>

const ISSUE_LABELS: Record<string, string> = {
  air_conditioning: 'điều hoà',
  electrical: 'hệ thống điện',
  plumbing: 'hệ thống nước',
  other: 'hạng mục khác',
}

const INTEREST_LABELS: Record<string, string> = {
  buy: 'mua',
  rent: 'thuê',
  consultation: 'nhận tư vấn',
}

const MOVE_VEHICLE_LABELS: Record<string, string> = {
  none: 'không dùng xe vận chuyển',
  van: 'dùng xe tải nhỏ',
  truck: 'dùng xe tải',
}

function keyOf(capability: Capability, field: string): string {
  return `${capability.name}::${field}`
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
}) {
  return (
    <label className="block text-sm text-gray-700 dark:text-gray-300">
      {label}
      <select
        required
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900"
      >
        <option value="">— Chọn —</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
  )
}

function InputField({
  label,
  value,
  type = 'text',
  placeholder,
  onChange,
}: {
  label: string
  value: string
  type?: 'text' | 'date' | 'time'
  placeholder?: string
  onChange: (value: string) => void
}) {
  return (
    <label className="block text-sm text-gray-700 dark:text-gray-300">
      {label}
      <input
        required
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900"
      />
    </label>
  )
}

/** Một form tổng hợp cho N dịch vụ đã chọn; browser chỉ dựng goal, không dựng TaskPlan. */
export function QuickActionForm({ capabilities, submitting, onSubmit, onCancel }: Props) {
  const [values, setValues] = useState<Values>({})
  const [projects, setProjects] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const needsProjects = capabilities.some((capability) => {
    const name = capability.name.toLocaleLowerCase('vi-VN')
    return name.includes('tham quan') || name.includes('quan tâm')
  })

  useEffect(() => {
    if (!needsProjects) return
    listProjects().then(setProjects).catch(() => setError('Chưa tải được danh sách dự án.'))
  }, [needsProjects])

  function getValue(capability: Capability, field: string): string {
    return values[keyOf(capability, field)] ?? ''
  }

  function setValue(capability: Capability, field: string, value: string) {
    setValues((previous) => ({ ...previous, [keyOf(capability, field)]: value }))
    setError(null)
  }

  function buildSegment(capability: Capability): string {
    const name = capability.name.toLocaleLowerCase('vi-VN')
    const value = (field: string) => getValue(capability, field)
    if (name.includes('tham quan')) {
      return `đặt lịch tham quan dự án ${value('project')} ngày ${value('date')} lúc ${value('time')}`
    }
    if (name.includes('quan tâm')) {
      return `đăng ký ${INTEREST_LABELS[value('interest')]} tại dự án ${value('project')}; tôi đồng ý để tư vấn viên liên hệ lúc ${value('contact')}`
    }
    if (name.includes('đỗ xe')) {
      const vehicle = value('vehicle') === 'car' ? 'ô tô' : 'xe máy'
      const zone = value('zone') === 'ZONE_A' ? 'Khu A' : 'Khu B'
      return `đăng ký ${vehicle} biển số ${value('plate')} và đặt chỗ đỗ xe tại ${zone} ngày ${value('date')}`
    }
    if (name.includes('bảo trì')) {
      return `báo sửa ${ISSUE_LABELS[value('issue')]} tại ${value('location')}: ${value('description')}; hẹn ngày ${value('date')} lúc ${value('time')}`
    }
    if (name.includes('chuyển nhà')) {
      const elevator = value('elevator') === 'yes' ? 'cần thang máy' : 'không cần thang máy'
      const loading = value('loading') === 'yes' ? 'cần hỗ trợ bốc xếp' : 'không cần hỗ trợ bốc xếp'
      return `đặt lịch chuyển nhà ngày ${value('date')} lúc ${value('time')}; ${elevator}, ${loading}, ${MOVE_VEHICLE_LABELS[value('vehicle')]}`
    }
    return 'thanh toán khoản phí đỗ xe của yêu cầu này sau khi hệ thống báo giá'
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      const segments = capabilities.map(buildSegment)
      await onSubmit(`Tôi muốn ${segments.join('; đồng thời ')}.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Chưa gửi được yêu cầu.')
    }
  }

  const projectOptions = projects.map((project) => ({ value: project, label: project }))

  function fieldsFor(capability: Capability) {
    const name = capability.name.toLocaleLowerCase('vi-VN')
    const value = (field: string) => getValue(capability, field)
    const change = (field: string) => (next: string) => setValue(capability, field, next)

    if (name.includes('tham quan')) return (
      <>
        <SelectField label="Dự án" value={value('project')} options={projectOptions} onChange={change('project')} />
        <InputField label="Ngày tham quan" type="date" value={value('date')} onChange={change('date')} />
        <InputField label="Giờ tham quan" type="time" value={value('time')} onChange={change('time')} />
      </>
    )
    if (name.includes('quan tâm')) return (
      <>
        <SelectField label="Dự án" value={value('project')} options={projectOptions} onChange={change('project')} />
        <SelectField label="Nhu cầu" value={value('interest')} options={[{ value: 'buy', label: 'Mua' }, { value: 'rent', label: 'Thuê' }, { value: 'consultation', label: 'Nhận tư vấn' }]} onChange={change('interest')} />
        {/* Giờ CỤ THỂ, không phải buổi.
            "Buổi chiều" tới tay nhân viên tư vấn vẫn không nói được nên gọi lúc
            mấy giờ, còn người dùng muốn hẹn đúng 14:30 thì không có cách nào
            chọn. Khung 08:00–18:00 là giờ làm việc của bộ phận tư vấn. */}
        <InputField label="Giờ muốn được liên hệ (08:00–18:00)" type="time" value={value('contact')} onChange={change('contact')} />
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input required type="checkbox" checked={value('consent') === 'yes'} onChange={(event) => setValue(capability, 'consent', event.target.checked ? 'yes' : '')} />
          Tôi đồng ý để nhân viên tư vấn liên hệ
        </label>
      </>
    )
    if (name.includes('đỗ xe')) return (
      <>
        <InputField label="Biển số xe" value={value('plate')} placeholder="Ví dụ: 30A-12345" onChange={change('plate')} />
        <SelectField label="Loại xe" value={value('vehicle')} options={[{ value: 'car', label: 'Ô tô' }, { value: 'motorcycle', label: 'Xe máy' }]} onChange={change('vehicle')} />
        <InputField label="Ngày đặt chỗ" type="date" value={value('date')} onChange={change('date')} />
        <SelectField label="Khu vực đỗ xe" value={value('zone')} options={[{ value: 'ZONE_A', label: 'Khu A' }, { value: 'ZONE_B', label: 'Khu B' }]} onChange={change('zone')} />
      </>
    )
    if (name.includes('bảo trì')) return (
      <>
        <SelectField label="Hạng mục" value={value('issue')} options={[{ value: 'air_conditioning', label: 'Điều hoà' }, { value: 'electrical', label: 'Điện' }, { value: 'plumbing', label: 'Nước' }, { value: 'other', label: 'Khác' }]} onChange={change('issue')} />
        <InputField label="Vị trí" value={value('location')} placeholder="Ví dụ: bếp" onChange={change('location')} />
        <InputField label="Mô tả sự cố" value={value('description')} onChange={change('description')} />
        <InputField label="Ngày mong muốn" type="date" value={value('date')} onChange={change('date')} />
        <InputField label="Giờ mong muốn" type="time" value={value('time')} onChange={change('time')} />
      </>
    )
    if (name.includes('chuyển nhà')) return (
      <>
        <InputField label="Ngày chuyển nhà" type="date" value={value('date')} onChange={change('date')} />
        <InputField label="Giờ chuyển nhà" type="time" value={value('time')} onChange={change('time')} />
        <SelectField label="Thang máy" value={value('elevator')} options={[{ value: 'yes', label: 'Cần sử dụng' }, { value: 'no', label: 'Không cần' }]} onChange={change('elevator')} />
        <SelectField label="Hỗ trợ bốc xếp" value={value('loading')} options={[{ value: 'yes', label: 'Cần hỗ trợ' }, { value: 'no', label: 'Không cần' }]} onChange={change('loading')} />
        <SelectField label="Phương tiện vận chuyển" value={value('vehicle')} options={[{ value: 'none', label: 'Không cần' }, { value: 'van', label: 'Xe tải nhỏ' }, { value: 'truck', label: 'Xe tải' }]} onChange={change('vehicle')} />
      </>
    )
    return <p className="text-sm text-gray-600 dark:text-gray-300">P-118 sẽ lấy báo giá từ yêu cầu đặt chỗ. Bạn không cần nhập số tiền.</p>
  }

  return (
    <form
      aria-label="Chuẩn bị các dịch vụ đã chọn"
      data-quick-action-form={capabilities.map((item) => item.name).join('|')}
      onSubmit={submit}
      className="mt-3 rounded-2xl border border-teal-200 bg-teal-50 p-4 dark:border-teal-900/50 dark:bg-teal-950/30"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {capabilities.length} dịch vụ đã chọn
          </p>
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">Điền một lần, P-118 sẽ tự sắp xếp thứ tự thực hiện.</p>
        </div>
        <button type="button" onClick={onCancel} className="text-sm text-gray-500 hover:text-gray-900 dark:hover:text-gray-100">Bỏ chọn</button>
      </div>

      <div className="space-y-4">
        {capabilities.map((capability) => (
          <fieldset key={capability.name} className="rounded-xl border border-teal-200 bg-white/70 p-3 dark:border-teal-900/50 dark:bg-gray-900/50">
            <legend className="px-1 text-sm font-semibold text-gray-900 dark:text-gray-100">{capability.name}</legend>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">{fieldsFor(capability)}</div>
          </fieldset>
        ))}
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      <button type="submit" disabled={submitting} className="mt-4 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60">
        {submitting ? 'Đang gửi…' : `Gửi ${capabilities.length} dịch vụ`}
      </button>
    </form>
  )
}
