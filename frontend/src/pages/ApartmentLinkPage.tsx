import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { BadgeCheck, Clock, ExternalLink, Home, ShieldCheck, ShieldX } from 'lucide-react'

import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { myVerificationRecords } from '../lib/agentApi'
import { useAuth } from '../lib/auth'
import type { VerificationRecord } from '../lib/types'
import { latestApartmentRecord } from '../lib/verification'

/**
 * Cửa vào xác minh căn hộ — P-118 CHỈ dẫn đường, không nhận hồ sơ.
 *
 * Xác minh căn hộ không nằm trong 10 tool của Agent (`tool_contract.py`): một
 * đơn vị độc lập đối chiếu giấy tờ rồi quyết định. Trước đây form nộp nằm ngay
 * trong workspace, nên giao diện nói một đằng còn mô hình trách nhiệm một nẻo —
 * người dùng tưởng P-118 tự duyệt được, và câu trả lời của Agent ("mình không
 * tự làm thay được") mâu thuẫn với chính màn hình họ đang nhìn.
 *
 * Trang này giữ đúng phần P-118 làm được: cho biết đang ở bước nào, và dẫn sang
 * cổng của đơn vị xác thực (`/verify`) để nộp. Trạng thái đọc từ cùng một
 * `verification_records` mà cổng kia ghi, nên hai bên không bao giờ lệch nhau.
 */

const STATUS_VIEW: Record<
  VerificationRecord['status'],
  { label: string; hint: string; tone: string; Icon: typeof Home }
> = {
  PENDING: {
    label: 'Đang chờ đơn vị xác thực duyệt',
    hint: 'Hồ sơ của bạn đã được gửi. Dịch vụ cư dân mở ngay sau khi được duyệt.',
    tone: 'border-amber-200 bg-amber-50 text-amber-900',
    Icon: Clock,
  },
  APPROVED: {
    label: 'Đã được duyệt',
    hint: 'Tài khoản của bạn đã liên kết với căn hộ. Bạn dùng được đầy đủ dịch vụ cư dân.',
    tone: 'border-teal-200 bg-teal-50 text-teal-900',
    Icon: BadgeCheck,
  },
  REJECTED: {
    label: 'Chưa được duyệt',
    hint: 'Đơn vị xác thực chưa đối chiếu được thông tin. Bạn gửi lại hồ sơ với thông tin đã sửa nhé.',
    tone: 'border-red-200 bg-red-50 text-red-900',
    Icon: ShieldX,
  },
}

export function ApartmentLinkPage() {
  const { user } = useAuth()
  const [records, setRecords] = useState<VerificationRecord[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    myVerificationRecords()
      .then((rows) => {
        if (alive) setRecords(rows)
      })
      // Không chặn màn: nút sang cổng xác thực vẫn phải bấm được kể cả khi
      // không đọc được trạng thái.
      .catch(() => undefined)
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  // Đơn MỚI NHẤT. Backend trả `ORDER BY created_at` tăng dần nên `[0]` là đơn
  // cũ nhất — xem `lib/verification.ts`.
  const latest = latestApartmentRecord(records)
  const view = latest ? STATUS_VIEW[latest.status] : null
  const alreadyVerified = user?.resident_verification_status === 'VERIFIED'

  return (
    <WorkspaceShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-[1000px] px-12 pb-16 pt-12">
          <div className="space-y-5">
            <header>
              <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Xác minh căn hộ</h1>
              <p className="mt-1 text-sm text-gray-500">
                Việc xác thực chủ sở hữu do một đơn vị độc lập thực hiện, không phải P-118. Bấm nút
                bên dưới để sang cổng của họ và nộp hồ sơ.
              </p>
            </header>

            {loading && <div className="h-20 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-900" />}

            {!loading && view && latest && (
              <section className={`rounded-2xl border p-4 ${view.tone}`}>
                <div className="flex items-start gap-3">
                  <view.Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">{view.label}</p>
                    <p className="mt-1 text-sm opacity-90">{view.hint}</p>
                    <p className="mt-2 text-sm font-medium">{apartmentLabel(latest)}</p>
                    {latest.reject_reason && (
                      <p className="mt-1 text-sm opacity-90">Lý do: {latest.reject_reason}</p>
                    )}
                  </div>
                </div>
              </section>
            )}

            {!loading && alreadyVerified ? (
              <p className="text-sm text-gray-500">
                Tài khoản của bạn đã được liên kết căn hộ. Cần đổi căn hộ, bạn liên hệ ban quản lý nhé.
              </p>
            ) : (
              !loading && (
                <section className="rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800">
                  {/* Đã nộp rồi thì đừng dặn chuẩn bị giấy tờ nữa — họ vừa
                      làm xong việc đó, và câu dặn đọc như hồ sơ chưa được
                      nhận. */}
                  <p className="flex items-start gap-2 text-sm text-gray-600 dark:text-gray-400">
                    <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                    {latest?.status === 'PENDING'
                      ? 'Hồ sơ của bạn đang ở chỗ đơn vị xác thực. Sang cổng của họ để xem lại những gì đã gửi.'
                      : 'Bạn sẽ cần mã căn hộ, tên khu đô thị và ảnh giấy tờ nhà (sổ hồng, hợp đồng mua bán). Toàn bộ hồ sơ do đơn vị xác thực giữ và đối chiếu.'}
                  </p>
                  {/* `Link` nội bộ chứ không phải `<a target="_blank">`: cổng
                      xác thực dùng chung phiên đăng nhập, mở tab mới sẽ mất
                      `sessionStorage` và người dùng phải đăng nhập lại. */}
                  <Link
                    to="/verify"
                    className="mt-4 inline-flex items-center gap-2 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-teal-600"
                  >
                    {latest?.status === 'PENDING' ? 'Xem hồ sơ đã gửi' : 'Xác thực với đơn vị'}
                    <ExternalLink className="h-4 w-4" aria-hidden />
                  </Link>
                </section>
              )
            )}
          </div>
        </div>
      </div>
    </WorkspaceShell>
  )
}

function apartmentLabel(record: VerificationRecord): string {
  const c = record.claimed_data
  if (record.record_type === 'apartment' && 'apartment_code' in c) {
    return `${c.apartment_code}${c.residential_area ? ` · ${c.residential_area}` : ''}`
  }
  return record.record_id
}
