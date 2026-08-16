import { useCallback, useEffect, useState } from 'react'
import { BadgeCheck, CalendarCheck, Home, Inbox, ShieldX, X } from 'lucide-react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import {
  decideVerificationRecord,
  decideViewingApproval,
  listVerificationRecords,
  listViewingApprovals,
} from '../lib/agentApi'
import type {
  VerificationRecord,
  VerificationRecordType,
  ViewingApprovalRecord,
} from '../lib/types'

/**
 * Giao diện duyệt GỘP: một màn, ba tab Căn hộ / Xe / Tham quan.
 *
 * Provider (hoặc admin) xử lý mọi hồ sơ xác thực — căn hộ và xe — ở một chỗ,
 * và cả lịch tham quan đang chờ duyệt trong cổng /review.
 *
 * - Căn hộ / Xe: mỗi card hiện đúng thứ người duyệt cần: claim, ảnh giấy tờ,
 *   và với căn hộ, `ownership_match` (so khớp chủ hộ do provider tính — KHÔNG
 *   lộ owner_name).
 * - Tham quan: card hiện dự án, ngày giờ, số khách, xe đưa đón và PII người
 *   yêu cầu (người duyệt cần gọi lại khách). Duyệt sẽ đặt luôn xe đưa đón
 *   (~30 giây) nên nút báo "Đang xử lý… (~30 giây)".
 *
 * Từ chối BẮT BUỘC lý do — backend 422 nếu thiếu; modal chặn ngay trên UI.
 * Duyệt căn hộ mở quyền cư dân; duyệt xe tạo xe vào hệ thống; duyệt tham quan
 * xác nhận lịch + đặt xe (backend materialize rồi resume workflow).
 */

type TabKey = VerificationRecordType | 'viewing'

const TABS: { key: TabKey; label: string; icon: typeof Home }[] = [
  { key: 'apartment', label: 'Căn hộ', icon: Home },
  { key: 'vehicle', label: 'Xe', icon: Inbox },
  { key: 'viewing', label: 'Tham quan', icon: CalendarCheck },
]

/**
 * Đối tượng đang được chọn để từ chối — hiện modal bắt buộc lý do.
 * Discriminated union để `kind` thu hẹp đúng loại `record` (verification khác
 * viewing về hình dạng record, TS không tự suy được qua một trường riêng).
 */
type RejectTarget =
  | { kind: 'verification'; record: VerificationRecord; reason: string }
  | { kind: 'viewing'; record: ViewingApprovalRecord; reason: string }

function claimLabel(record: VerificationRecord): string {
  const c = record.claimed_data
  if (record.record_type === 'apartment' && 'apartment_code' in c) {
    return `${c.apartment_code} · ${c.residential_area ?? ''}`
  }
  if (record.record_type === 'vehicle' && 'plate_number' in c) {
    return c.plate_number ?? '—'
  }
  return record.record_id
}

function claimantName(record: VerificationRecord): string {
  const c = record.claimed_data
  if (record.record_type === 'apartment' && 'full_name' in c) {
    return c.full_name ?? ''
  }
  return ''
}

function viewingTitle(record: ViewingApprovalRecord): string {
  return record.project_name || record.project_id || record.workflow_id
}

/** Nhãn cho dòng tiêu đề của modal từ chối — khác nhau theo loại đối tượng. */
function rejectLabel(target: RejectTarget): string {
  return target.kind === 'viewing' ? viewingTitle(target.record) : claimLabel(target.record)
}

export function ProviderReviewPage() {
  const [tab, setTab] = useState<TabKey>('apartment')
  const [verificationItems, setVerificationItems] = useState<VerificationRecord[]>([])
  const [viewingItems, setViewingItems] = useState<ViewingApprovalRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [rejectTarget, setRejectTarget] = useState<RejectTarget | null>(null)

  const load = useCallback(async () => {
    try {
      // Tab Tham quan đọc bảng viewing_approvals (hình dạng record khác hẳn
      // VerificationRecord) — tách state chứ không ép chung một mảng.
      if (tab === 'viewing') {
        setViewingItems(await listViewingApprovals('AWAITING'))
      } else {
        setVerificationItems(await listVerificationRecords(tab, 'PENDING'))
      }
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không tải được danh sách hồ sơ.')
    } finally {
      setLoading(false)
    }
  }, [tab])

  useEffect(() => {
    setLoading(true)
    void load()
  }, [load])

  async function decideVerification(
    record: VerificationRecord,
    decision: 'approve' | 'reject',
    reason?: string,
  ) {
    if (busy) return
    setBusy(record.record_id)
    setError(null)
    setDone(null)
    try {
      const { item } = await decideVerificationRecord(record.record_id, {
        decision,
        ...(decision === 'reject' ? { reject_reason: reason } : {}),
      })
      setDone(
        decision === 'approve'
          ? item.record_type === 'vehicle'
            ? `Đã duyệt xe ${claimLabel(item)} — xe đã vào hệ thống.`
            : `Đã duyệt căn hộ — dịch vụ cư dân đã mở.`
          : 'Đã từ chối hồ sơ.',
      )
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không gửi được quyết định.')
    } finally {
      setBusy(null)
      setRejectTarget(null)
    }
  }

  /** Duyệt tham quan chạy nốt book_shuttle (~30s) ĐỒNG BỘ trong request — UI
   * phải báo rõ đang xử lý, không để người duyệt tưởng nút kẹt. */
  async function decideViewing(
    record: ViewingApprovalRecord,
    decision: 'approve' | 'reject',
    reason?: string,
  ) {
    if (busy) return
    setBusy(`viewing:${record.workflow_id}`)
    setError(null)
    setDone(null)
    try {
      await decideViewingApproval(record.workflow_id, {
        decision,
        ...(decision === 'reject' ? { reject_reason: reason } : {}),
      })
      setDone(
        decision === 'approve'
          ? 'Đã duyệt — lịch tham quan và xe đã được xác nhận.'
          : 'Đã từ chối lịch tham quan.',
      )
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không gửi được quyết định.')
    } finally {
      setBusy(null)
      setRejectTarget(null)
    }
  }

  /** Nút "Xác nhận từ chối" trong modal — chuyển đúng theo loại đối tượng. */
  async function confirmReject() {
    if (!rejectTarget || busy) return
    const reason = rejectTarget.reason.trim()
    if (!reason) return
    if (rejectTarget.kind === 'viewing') {
      await decideViewing(rejectTarget.record, 'reject', reason)
    } else {
      await decideVerification(rejectTarget.record, 'reject', reason)
    }
  }

  const emptyMessage =
    tab === 'viewing'
      ? 'Không có lịch tham quan nào đang chờ duyệt.'
      : `Không có hồ sơ ${tab === 'apartment' ? 'căn hộ' : 'xe'} nào đang chờ.`

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Hồ sơ chờ xác thực</h1>
        <p className="mt-1 text-sm text-gray-500">
          Một hàng chờ gộp cho căn hộ, xe và lịch tham quan. Căn hộ duyệt xong mở dịch vụ cư dân;
          xe duyệt xong được tạo vào hệ thống của đơn vị; lịch tham quan duyệt xong sẽ đặt xe đưa đón
          (chờ ~30 giây). Từ chối phải kèm lý do.
        </p>
      </header>

      {/* Tabs Căn hộ / Xe / Tham quan */}
      <div className="flex gap-1 rounded-xl border border-gray-200 bg-card p-1 dark:border-gray-800">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              tab === key
                ? 'bg-indigo-700 text-white'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
            }`}
          >
            <Icon className="h-4 w-4" aria-hidden />
            {label}
          </button>
        ))}
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30" role="alert">
          {error}
        </p>
      )}
      {done && (
        <p className="rounded-xl bg-indigo-50 p-3 text-sm text-indigo-800 dark:bg-indigo-950/30" role="status">
          {done}
        </p>
      )}

      {loading && <SkeletonRows count={3} />}
      {!loading && (tab === 'viewing' ? viewingItems.length === 0 : verificationItems.length === 0) && (
        <EmptyState message={emptyMessage} />
      )}

      {/* Hồ sơ căn hộ / xe */}
      {tab !== 'viewing' && (
        <ul className="space-y-3">
          {verificationItems.map((record) => (
            <li
              key={record.record_id}
              className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {claimLabel(record)}
                    </p>
                    {record.record_type === 'apartment' && claimantName(record) && (
                      <span className="text-sm text-gray-600 dark:text-gray-400">
                        — {claimantName(record)}
                      </span>
                    )}
                  </div>

                  {record.record_type === 'apartment' && (
                    <p className="mt-1 text-xs">
                      {record.ownership_match === true ? (
                        <span className="font-medium text-indigo-700 dark:text-indigo-300">
                          Khớp chủ hộ trong hồ sơ căn hộ
                        </span>
                      ) : record.ownership_match === false ? (
                        <span className="font-medium text-amber-700 dark:text-amber-300">
                          KHÔNG khớp chủ hộ trong hồ sơ
                        </span>
                      ) : (
                        <span className="text-gray-500">Đang chờ tính đối chiếu chủ hộ…</span>
                      )}
                    </p>
                  )}

                  <p className="mt-1 text-xs text-gray-500">
                    Gửi lúc {new Date(record.created_at).toLocaleString('vi-VN')}
                  </p>

                  {record.proof_image_urls.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {record.proof_image_urls.map((url) => (
                        <a
                          key={url}
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs text-gray-700 hover:border-indigo-700 hover:text-indigo-700 dark:border-gray-700 dark:text-gray-300"
                        >
                          Ảnh giấy tờ
                        </a>
                      ))}
                    </div>
                  )}
                </div>

                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => decideVerification(record, 'approve')}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-2 rounded-xl bg-indigo-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
                  >
                    <BadgeCheck className="h-4 w-4" aria-hidden />
                    {busy === record.record_id ? 'Đang xử lý…' : 'Duyệt'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setRejectTarget({ kind: 'verification', record, reason: '' })}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-2 rounded-xl border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200"
                  >
                    <ShieldX className="h-4 w-4" aria-hidden />
                    Từ chối
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Lịch tham quan chờ duyệt — record khác VerificationRecord nên render riêng. */}
      {tab === 'viewing' && (
        <ul className="space-y-3">
          {viewingItems.map((record) => (
            <li
              key={record.workflow_id}
              className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    {viewingTitle(record)}
                  </p>
                  <p className="mt-1 text-xs text-gray-500">
                    {record.viewing_date} · {record.viewing_time}
                  </p>
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                    <dt className="text-gray-500 dark:text-gray-400">Mã dự án</dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">{record.project_id}</dd>
                    <dt className="text-gray-500 dark:text-gray-400">Số khách</dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {record.passenger_count != null ? `${record.passenger_count} người` : '—'}
                    </dd>
                    <dt className="text-gray-500 dark:text-gray-400">Xe đưa đón</dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {record.wants_shuttle ? 'Có — đặt sau khi duyệt' : 'Không'}
                    </dd>
                    {record.applicant_name && (
                      <>
                        <dt className="text-gray-500 dark:text-gray-400">Người yêu cầu</dt>
                        <dd className="font-medium text-gray-900 dark:text-gray-100">
                          {record.applicant_name}
                        </dd>
                      </>
                    )}
                    {record.applicant_phone && (
                      <>
                        <dt className="text-gray-500 dark:text-gray-400">SĐT</dt>
                        <dd className="font-medium text-gray-900 dark:text-gray-100">
                          {record.applicant_phone}
                        </dd>
                      </>
                    )}
                  </dl>
                </div>

                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => decideViewing(record, 'approve')}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-2 rounded-xl bg-indigo-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
                  >
                    <CalendarCheck className="h-4 w-4" aria-hidden />
                    {busy === `viewing:${record.workflow_id}` ? 'Đang xử lý… (~30 giây)' : 'Duyệt'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setRejectTarget({ kind: 'viewing', record, reason: '' })}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-2 rounded-xl border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200"
                  >
                    <ShieldX className="h-4 w-4" aria-hidden />
                    Từ chối
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="flex items-start gap-2 text-xs text-gray-500">
        <ShieldX className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        Bạn chỉ thấy đối chiếu chủ hộ (khớp / không khớp) — không thấy tên chủ hộ. Dữ liệu chủ hộ không
        rời khỏi provider.
      </p>

      {/* Modal từ chối — bắt buộc lý do */}
      {rejectTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
          <div className="absolute inset-0 bg-black/50" onClick={() => setRejectTarget(null)} aria-hidden />
          <div className="relative w-full max-w-md rounded-2xl bg-white p-5 shadow-xl dark:bg-gray-900">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                {rejectTarget.kind === 'viewing' ? 'Từ chối lịch tham quan' : 'Từ chối hồ sơ'}
              </h2>
              <button
                type="button"
                aria-label="Đóng"
                onClick={() => setRejectTarget(null)}
                className="rounded-lg p-1 text-gray-400 hover:text-gray-600"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>
            <p className="mt-1 text-sm text-gray-500">
              {rejectLabel(rejectTarget)} — vui lòng nêu lý do để người yêu cầu biết.
            </p>
            <textarea
              value={rejectTarget.reason}
              onChange={(e) =>
                setRejectTarget({ ...rejectTarget, reason: e.target.value })
              }
              rows={3}
              placeholder="Ví dụ: ảnh giấy tờ mờ, chưa đối chiếu được…"
              className="mt-3 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setRejectTarget(null)}
                className="rounded-xl border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 dark:border-gray-700 dark:text-gray-200"
              >
                Huỷ
              </button>
              <button
                type="button"
                disabled={!rejectTarget.reason.trim() || busy !== null}
                onClick={() => void confirmReject()}
                className="inline-flex items-center gap-2 rounded-xl bg-red-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                <ShieldX className="h-4 w-4" aria-hidden />
                Xác nhận từ chối
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
