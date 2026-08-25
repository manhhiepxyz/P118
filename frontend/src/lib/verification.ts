import type { VerificationRecord } from './types'

/**
 * Đơn xác minh căn hộ MỚI NHẤT của người dùng.
 *
 * Backend trả `ORDER BY created_at` — TĂNG DẦN, tức phần tử [0] là đơn CŨ
 * NHẤT. Cả `ApartmentLinkPage` lẫn `VerifyApartmentPage` đều lấy `records[0]`
 * rồi đặt tên biến là `latest`, nên chúng hiển thị đúng đơn cũ nhất.
 *
 * Tái hiện được: gửi đơn → bị từ chối → gửi lại. `GET /my` trả
 * `[REJECTED, PENDING]`. Người dùng vừa nộp lại xong vẫn thấy banner đỏ "Chưa
 * được duyệt" kèm lý do từ chối cũ, còn form nộp vẫn mở (vì trạng thái đang
 * đọc là REJECTED, không phải PENDING) nên họ nộp thêm lần nữa và ăn 409. Họ
 * không có cách nào biết đơn thật của mình đang chờ duyệt bình thường.
 *
 * Sắp xếp ở client thay vì sửa `ORDER BY` phía provider: thứ tự tăng dần là
 * hợp lý cho màn duyệt (đơn cũ lên trước, ai chờ lâu nhất được xử lý trước).
 * Chỗ sai là giả định của UI, nên sửa ở UI.
 *
 * Hàm dùng chung để hai trang không lệch nhau lần nữa — chúng đang hiển thị
 * cùng một sự thật cho cùng một người.
 */
export function latestApartmentRecord(records: VerificationRecord[]): VerificationRecord | null {
  // So bằng `Date.parse`, không so chuỗi. Chuỗi ISO chỉ sắp đúng khi mọi bản
  // ghi cùng một offset múi giờ; hôm nay chúng đều là `+00:00`, nhưng đó là
  // chi tiết của serializer chứ không phải điều đã hứa.
  let latest: VerificationRecord | null = null
  let latestAt = -Infinity
  for (const record of records) {
    if (record.record_type !== 'apartment') continue
    const at = Date.parse(record.created_at)
    if (Number.isNaN(at)) continue
    if (at > latestAt) {
      latest = record
      latestAt = at
    }
  }
  return latest
}
