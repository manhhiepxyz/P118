/**
 * Chặn dưới và chặn trên cho mọi ô ngày.
 *
 * `TaskPlanValidator` của backend từ chối ngày quá khứ và ngày vượt chân trời.
 * Nó từ chối ĐÚNG, nhưng từ chối SAU khi người dùng đã gõ xong và đã chờ — đo
 * được `plan 65,49s` cho một yêu cầu mang ngày hôm qua.
 *
 * `<input type="date">` nhận sẵn `min`/`max`: đặt chúng thì lịch chọn tự mờ
 * những ngày không hợp lệ. Không có gì thông minh ở đây — chỉ là nói ra sớm
 * điều backend sẽ nói muộn.
 *
 * Con số chân trời lấy đúng của backend (`src/common/schedule_policy.py`), và
 * `tests/test_the_form_will_not_take_a_past_date.py` đối chiếu hai nơi: viết
 * cứng ở đây rồi đổi bên kia sẽ để lại một biểu mẫu cho gõ những ngày backend
 * vừa thôi nhận.
 */

/** Khớp `MAX_HORIZON_DAYS` trong `src/common/schedule_policy.py`. */
export const MAX_HORIZON_DAYS = 1825

function iso(d: Date): string {
  // Dùng giờ ĐỊA PHƯƠNG, không phải `toISOString()`.
  //
  // `toISOString()` đổi sang UTC trước; ở múi giờ +07, mọi lúc trước 07:00 sáng
  // sẽ cho ra NGÀY HÔM QUA — và chặn dưới tự nó cho phép đúng cái ngày nó phải
  // chặn, đúng vào lúc người dùng dễ gặp nhất.
  const thang = String(d.getMonth() + 1).padStart(2, '0')
  const ngay = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${thang}-${ngay}`
}

/** Hôm nay, dạng `YYYY-MM-DD`. Ngày sớm nhất người dùng chọn được. */
export function minDate(): string {
  return iso(new Date())
}

/** Ngày xa nhất backend còn nhận. */
export function maxDate(): string {
  const d = new Date()
  d.setDate(d.getDate() + MAX_HORIZON_DAYS)
  return iso(d)
}
