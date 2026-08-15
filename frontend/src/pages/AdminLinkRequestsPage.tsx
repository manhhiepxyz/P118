import { useCallback, useEffect, useState } from 'react'
import { BadgeCheck, Inbox, ShieldX } from 'lucide-react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import { decideLinkRequest, listLinkRequests } from '../lib/agentApi'
import type { AdminLinkRequestItem } from '../lib/types'

/**
 * Hàng chờ duyệt liên kết căn hộ.
 *
 * Thay cho màn nhập tay trước đây, nơi admin phải gõ UUID tài khoản và mã cư
 * dân. Ngoài chuyện bất tiện, nó còn sai về bản chất: admin không có cách nào
 * biết hai giá trị đó ngoài việc hỏi chính người dùng — mà hỏi rồi gõ lại thì
 * không có gì bảo đảm gõ đúng người.
 *
 * Ở đây admin chỉ chọn duyệt hoặc từ chối. Tài khoản nào, căn hộ nào đều đọc
 * từ dòng yêu cầu đã ghim; browser không gửi và không sửa được.
 *
 * Tên hiển thị đã được BACKEND mask. Frontend không mask hộ: mask ở client
 * nghĩa là dữ liệu đầy đủ vẫn đi qua mạng và vẫn nằm trong tab Network.
 */
export function AdminLinkRequestsPage() {
  const [items, setItems] = useState<AdminLinkRequestItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setItems(await listLinkRequests('PENDING'))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không tải được danh sách.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function decide(requestId: string, decision: 'approve' | 'reject') {
    if (busy) return
    setBusy(requestId)
    setError(null)
    setDone(null)
    try {
      await decideLinkRequest(requestId, decision)
      setDone(decision === 'approve' ? 'Đã duyệt và mở dịch vụ cư dân.' : 'Đã từ chối yêu cầu.')
      // Đọc lại từ server thay vì tự xoá khỏi danh sách: nếu một admin khác vừa
      // xử lý xong, danh sách tự dựng ở client sẽ lệch với sự thật.
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không gửi được quyết định.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Yêu cầu liên kết căn hộ</h1>
        <p className="mt-1 text-sm text-gray-500">
          Cư dân gửi thông tin căn hộ; bạn xác nhận. Duyệt sẽ tạo hoặc nối hồ sơ cư dân và mở dịch vụ
          cho tài khoản đó.
        </p>
      </header>

      {error && (
        <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30" role="alert">
          {error}
        </p>
      )}
      {done && (
        <p className="rounded-xl bg-teal-50 p-3 text-sm text-teal-800 dark:bg-teal-950/30" role="status">
          {done}
        </p>
      )}

      {loading && <SkeletonRows count={3} />}
      {!loading && items.length === 0 && <EmptyState message="Không có yêu cầu nào đang chờ." />}

      <ul className="space-y-3">
        {items.map((item) => (
          <li
            key={item.request_id}
            className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  {item.apartment_code} · {item.residential_area}
                </p>
                <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
                  {item.full_name} — tài khoản <span className="font-mono">{item.username}</span>
                </p>
              </div>

              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => decide(item.request_id, 'approve')}
                  disabled={busy !== null}
                  className="inline-flex items-center gap-2 rounded-xl bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
                >
                  <BadgeCheck className="h-4 w-4" aria-hidden />
                  {busy === item.request_id ? 'Đang gửi…' : 'Duyệt'}
                </button>
                <button
                  type="button"
                  onClick={() => decide(item.request_id, 'reject')}
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

      <p className="flex items-start gap-2 text-xs text-gray-500">
        <Inbox className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        Gate 2: xác minh là thao tác thủ công, chưa có eKYC. Hãy đối chiếu với hồ sơ căn hộ của ban
        quản lý trước khi duyệt.
      </p>
    </div>
  )
}
