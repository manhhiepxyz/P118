import { useEffect, useMemo, useState } from "react";

import {
  adminRequestDetail,
  adminRequests,
  type AdminDecisionStatus,
  type AdminRequestDetail,
  type AdminRequestListItem,
  type AdminWaitingFor,
} from "../lib/agentApi";
import { useToast } from "../lib/toast";
import { AlertTriangle, Clock, Eye, Search, X } from "lucide-react";

/**
 * Màn GIÁM SÁT của admin. Chỉ đọc.
 *
 * Không có Duyệt, Từ chối, Retry, Chạy tiếp, Xác nhận thanh toán, và không có
 * link tới `/review`. Quyền duyệt thuộc về ĐƠN VỊ CUNG CẤP; admin ở đây để
 * biết hệ thống đang có việc gì và kẹt ở đâu.
 *
 * Trang này từng gọi `/admin/workflows/history` (trả `goal` thô và
 * `failed_task.input` — nội dung người dùng gõ, chưa qua lọc) và có một nút
 * Retry gọi `POST /admin/workflows/{id}/retry`, endpoint đặt thẳng
 * `workflows.status = PENDING` không điều kiện. Cả hai endpoint đã bị xoá.
 *
 * Nhãn dịch vụ và trạng thái bước do BACKEND trả về, đã là tiếng Việt. Trang
 * này cố ý KHÔNG giữ bảng tra tool → tên: một bảng thứ hai là một bảng sẽ lệch,
 * và lệch theo hướng phơi tên hàm nội bộ ra màn hình.
 */

const TRANG_THAI_LUONG: Record<string, string> = {
  PENDING: "Đang chuẩn bị",
  RUNNING: "Đang chạy",
  WAITING_APPROVAL: "Đang chờ",
  SUCCESS: "Hoàn tất",
  FAILED: "Không thành công",
  CANCELLED: "Đã huỷ",
};

const DANG_CHO: Record<AdminWaitingFor, string> = {
  PROVIDER: "Chờ đơn vị cung cấp",
  CUSTOMER_PAYMENT: "Chờ khách xác nhận thanh toán",
  NONE: "Không chờ ai",
};

const QUYET_DINH: Record<AdminDecisionStatus, string> = {
  AWAITING: "Chưa quyết định",
  APPROVED: "Đã duyệt",
  REJECTED: "Đã từ chối",
  NONE: "Không cần duyệt",
};

const MAU_QUYET_DINH: Record<AdminDecisionStatus, string> = {
  AWAITING: "bg-amber-100 text-amber-800",
  APPROVED: "bg-emerald-100 text-emerald-800",
  REJECTED: "bg-rose-100 text-rose-800",
  NONE: "bg-gray-100 text-gray-600",
};

function nhan(value: string | null | undefined, bang: Record<string, string>): string {
  if (!value) return "—";
  return bang[value] ?? value;
}

function thoiDiem(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("vi-VN");
}

function Chip({ text, tone }: { text: string; tone: string }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>{text}</span>
  );
}

export function AdminWorkflowsPage() {
  const toast = useToast();
  const [items, setItems] = useState<AdminRequestListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<AdminRequestDetail | null>(null);
  const limit = 20;

  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(search);
      setPage(1);
    }, 400);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    let huy = false;
    setLoading(true);
    adminRequests(page, limit, debounced || undefined)
      .then((data) => {
        if (huy) return;
        setItems(data.items);
        setTotal(data.total);
      })
      .catch(() => {
        if (!huy) toast.push("error", "Không tải được danh sách yêu cầu.");
      })
      .finally(() => {
        if (!huy) setLoading(false);
      });
    return () => {
      huy = true;
    };
  }, [page, debounced, toast]);

  const soTrang = useMemo(() => Math.max(1, Math.ceil(total / limit)), [total]);

  async function moChiTiet(workflowId: string) {
    try {
      setDetail(await adminRequestDetail(workflowId));
    } catch {
      toast.push("error", "Không mở được chi tiết yêu cầu.");
    }
  }

  return (
    <div className="space-y-5 p-6">
      <header>
        <h1 className="text-xl font-semibold text-gray-900">Yêu cầu trong hệ thống</h1>
        <p className="mt-1 text-sm text-gray-500">
          Màn hình theo dõi. Quyết định duyệt hay từ chối thuộc về đơn vị cung cấp dịch vụ.
        </p>
      </header>

      <label className="relative block max-w-sm">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <span className="sr-only">Tìm theo tài khoản</span>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Tìm theo tài khoản…"
          className="h-10 w-full rounded-xl border border-gray-300 bg-white pl-9 pr-3 text-sm outline-none focus:border-teal-600"
        />
      </label>

      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="px-4 py-3">Tài khoản</th>
              <th className="px-4 py-3">Yêu cầu</th>
              <th className="px-4 py-3">Dịch vụ</th>
              <th className="px-4 py-3">Trạng thái</th>
              <th className="px-4 py-3">Đang chờ</th>
              <th className="px-4 py-3">Đơn vị</th>
              <th className="px-4 py-3">Cập nhật</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-500">
                  Đang tải…
                </td>
              </tr>
            )}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-500">
                  Chưa có yêu cầu nào.
                </td>
              </tr>
            )}
            {!loading &&
              items.map((item) => (
                <tr key={item.workflow_id} className="align-top hover:bg-gray-50">
                  <td className="px-4 py-3 font-medium text-gray-900">
                    {item.account.display_name ?? item.account.username ?? "—"}
                  </td>
                  <td className="max-w-xs px-4 py-3 text-gray-700">{item.goal ?? "—"}</td>
                  <td className="px-4 py-3 text-gray-700">
                    {item.service_names.length ? item.service_names.join(", ") : "—"}
                  </td>
                  <td className="px-4 py-3">{nhan(item.workflow_status, TRANG_THAI_LUONG)}</td>
                  <td className="px-4 py-3">
                    {item.waiting_for === "NONE" ? (
                      <span className="text-gray-500">—</span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-amber-700">
                        <Clock className="h-3.5 w-3.5" />
                        {DANG_CHO[item.waiting_for]}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <Chip
                      text={QUYET_DINH[item.provider_decision_status]}
                      tone={MAU_QUYET_DINH[item.provider_decision_status]}
                    />
                  </td>
                  <td className="px-4 py-3 text-gray-500">{thoiDiem(item.updated_at)}</td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => moChiTiet(item.workflow_id)}
                      className="inline-flex items-center gap-1 rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs text-gray-700 hover:bg-gray-100"
                    >
                      <Eye className="h-3.5 w-3.5" />
                      Xem
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-gray-600">
        <span>
          Trang {page}/{soTrang} · {total} yêu cầu
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-lg border border-gray-300 px-3 py-1.5 disabled:opacity-40"
          >
            Trước
          </button>
          <button
            type="button"
            disabled={page >= soTrang}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-lg border border-gray-300 px-3 py-1.5 disabled:opacity-40"
          >
            Sau
          </button>
        </div>
      </div>

      {detail && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6">
          <div className="w-full max-w-3xl rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Chi tiết yêu cầu</h2>
                <p className="mt-1 text-sm text-gray-500">
                  {detail.account.display_name ?? detail.account.username ?? "—"} ·{" "}
                  {nhan(detail.workflow_status, TRANG_THAI_LUONG)}
                </p>
              </div>
              <button
                type="button"
                aria-label="Đóng"
                onClick={() => setDetail(null)}
                className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <p className="mt-4 text-sm text-gray-800">{detail.goal ?? "—"}</p>

            <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
              <div>
                <dt className="text-gray-500">Đang chờ</dt>
                <dd className="text-gray-900">{DANG_CHO[detail.waiting_for]}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Đơn vị cung cấp</dt>
                <dd className="text-gray-900">{QUYET_DINH[detail.provider_decision_status]}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Thanh toán</dt>
                <dd className="text-gray-900">{QUYET_DINH[detail.payment_decision_status]}</dd>
              </div>
            </dl>

            <h3 className="mt-6 text-sm font-semibold text-gray-900">Các bước</h3>
            <ul className="mt-2 divide-y divide-gray-100 rounded-xl border border-gray-200">
              {detail.steps.map((step) => (
                <li key={step.task_id} className="px-4 py-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-gray-900">{step.service_name}</span>
                    <span className="text-gray-500">{step.status ?? "—"}</span>
                  </div>
                  {step.decided_by && (
                    <p className="mt-1 text-xs text-gray-500">
                      {step.decided_by.display_name} quyết định lúc {thoiDiem(step.decided_at)}
                    </p>
                  )}
                  {step.reject_reason && (
                    <p className="mt-1 text-xs text-rose-700">Lý do: {step.reject_reason}</p>
                  )}
                  {step.failure_summary && (
                    <p className="mt-1 inline-flex items-start gap-1 text-xs text-amber-700">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {step.failure_summary}
                    </p>
                  )}
                </li>
              ))}
            </ul>

            {detail.payment && (
              <p className="mt-4 text-sm text-gray-700">
                Khoản thanh toán: {detail.payment.amount.toLocaleString("vi-VN")}{" "}
                {detail.payment.currency} · {QUYET_DINH[detail.payment.status as AdminDecisionStatus] ?? detail.payment.status}
              </p>
            )}

            {detail.history.length > 0 && (
              <>
                <h3 className="mt-6 text-sm font-semibold text-gray-900">Diễn biến</h3>
                <ol className="mt-2 space-y-1 text-xs text-gray-600">
                  {detail.history.map((event, index) => (
                    <li key={`${event.stage}-${index}`}>
                      {thoiDiem(event.at)} — {event.stage}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default AdminWorkflowsPage;
