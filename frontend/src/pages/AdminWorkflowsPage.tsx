import React, { useState, useEffect } from "react";
import {
  adminWorkflowsHistory,
  adminRetryWorkflow,
  type AdminWorkflowHistoryItem,
} from "../lib/agentApi";
import { useToast } from "../lib/toast";
import {
  Activity,
  RotateCcw,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Search,
  Eye,
  ChevronUp
} from "lucide-react";

const ERROR_CODE_MAP: Record<string, string> = {
  MISSING_INFORMATION: "Thiếu thông tin bắt buộc",
  INVALID_INPUT: "Dữ liệu không hợp lệ",
  RESIDENT_NOT_FOUND: "Không tìm thấy cư dân",
  RESIDENT_ALREADY_EXISTS: "Cư dân đã tồn tại",
  VEHICLE_NOT_FOUND: "Không tìm thấy xe",
  VEHICLE_ALREADY_EXISTS: "Xe đã được đăng ký",
  BOOKING_NOT_FOUND: "Không tìm thấy lượt đặt",
  BOOKING_ALREADY_EXISTS: "Lượt đặt đã tồn tại",
  PAYMENT_NOT_FOUND: "Không tìm thấy giao dịch",
  NO_AVAILABILITY: "Hết chỗ/Không khả dụng",
  PAYMENT_FAILED: "Thanh toán thất bại",
  PROJECT_NOT_FOUND: "Không tìm thấy dự án",
  VIEWING_ALREADY_BOOKED: "Lịch xem nhà đã được đặt",
  INTEREST_ALREADY_EXISTS: "Đã đăng ký quan tâm",
  SHUTTLE_ALREADY_BOOKED: "Xe đưa đón đã được đặt",
  VIEWING_NOT_FOUND: "Không tìm thấy lịch xem nhà",
  SERVICE_TIMEOUT: "Hết thời gian chờ dịch vụ (Timeout)",
  SERVICE_UNAVAILABLE: "Dịch vụ hiện không khả dụng",
  INTERNAL_SERVICE_ERROR: "Lỗi hệ thống nội bộ",
  UNKNOWN_EXTERNAL_ERROR: "Lỗi không xác định từ hệ thống ngoài",
  INVALID_TASK_PLAN: "Kế hoạch luồng không hợp lệ",
  UNKNOWN_TOOL: "Công cụ không được hỗ trợ",
  DEPENDENCY_ERROR: "Lỗi phụ thuộc (Dependency Error)",
  APPROVAL_REQUIRED: "Cần xác nhận từ người dùng",
  ACTION_DENIED: "Hành động bị từ chối",
  EXECUTION_ERROR: "Lỗi thực thi (Execution Error)",
};

const TOOL_MAP: Record<string, string> = {
  register_resident: "Đăng ký thông tin cư dân",
  register_vehicle: "Đăng ký xe",
  book_parking: "Đặt chỗ đỗ xe",
  pay_fee: "Thanh toán phí",
  search_properties: "Tìm kiếm bất động sản",
  schedule_property_viewing: "Đặt lịch xem nhà",
  create_maintenance_request: "Tạo yêu cầu bảo trì",
  schedule_move: "Đặt lịch chuyển nhà",
  register_property_interest: "Đăng ký quan tâm BĐS",
  book_shuttle: "Đặt xe đưa đón",
};

export function AdminWorkflowsPage() {
  const [workflows, setWorkflows] = useState<AdminWorkflowHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [searchUser, setSearchUser] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const limit = 50;
  const toast = useToast();

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchUser);
      setPage(1); // Reset page on search
    }, 500);
    return () => clearTimeout(timer);
  }, [searchUser]);

  const fetchWorkflows = async () => {
    setLoading(true);
    try {
      const data = await adminWorkflowsHistory(page, limit, debouncedSearch);
      setWorkflows(data.items);
    } catch (error: any) {
      toast.push("error", error.message || "Lỗi khi tải lịch sử luồng");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWorkflows();
  }, [page, debouncedSearch]);

  const handleRetry = async (workflowId: string) => {
    try {
      await adminRetryWorkflow(workflowId);
      toast.push("success", "Đã gửi yêu cầu chạy lại");
      fetchWorkflows();
    } catch (error: any) {
      toast.push("error", error.message || "Lỗi khi yêu cầu chạy lại");
    }
  };

  const toggleRow = (id: string) => {
    setExpandedRow(expandedRow === id ? null : id);
  };

  return (
    <div className="p-8 max-w-[1400px] mx-auto min-h-full">
      <div className="flex items-center justify-between gap-4 mb-8">
        <div className="flex items-center gap-4">
          <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-500 shadow-inner">
            <Activity className="w-6 h-6" strokeWidth={2} />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[var(--text-primary)]">Lịch sử Luồng hệ thống</h1>
            <p className="text-sm text-[var(--text-secondary)] mt-1">Giám sát và kiểm tra chi tiết các chuỗi xử lý nghiệp vụ</p>
          </div>
        </div>
        
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-secondary)]" />
          <input
            type="text"
            placeholder="Tìm theo người dùng..."
            value={searchUser}
            onChange={(e) => setSearchUser(e.target.value)}
            className="pl-10 pr-4 py-2.5 bg-[var(--surface)]/80 backdrop-blur-md border border-[var(--border-light)] rounded-xl text-sm focus:outline-none focus:border-[var(--primary)] transition-all w-72 shadow-sm hover:shadow-md"
          />
        </div>
      </div>

      <div className="bg-[var(--surface)]/80 backdrop-blur-md border border-[var(--border-light)] rounded-2xl overflow-hidden shadow-sm hover:shadow-md transition-shadow">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-[var(--surface-hover)] border-b border-[var(--border-light)]">
                <th className="p-4 font-medium text-[var(--text-secondary)] w-24">ID</th>
                <th className="p-4 font-medium text-[var(--text-secondary)] w-32">Người dùng</th>
                <th className="p-4 font-medium text-[var(--text-secondary)] w-48">Các bước</th>
                <th className="p-4 font-medium text-[var(--text-secondary)] w-32">Trạng thái</th>
                <th className="p-4 font-medium text-[var(--text-secondary)] w-48">Cập nhật</th>
                <th className="p-4 font-medium text-[var(--text-secondary)] text-right w-40">Thao tác</th>
              </tr>
            </thead>
            <tbody className="relative">
              {loading && workflows.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-[var(--text-secondary)]">Đang tải...</td>
                </tr>
              )}
              {workflows.map((wf) => (
                <React.Fragment key={wf.workflow_id}>
                  <tr className={`border-b border-[var(--border-light)] hover:bg-[var(--surface-hover)] transition-colors ${expandedRow === wf.workflow_id ? 'bg-[var(--surface-hover)]' : ''}`}>
                    <td className="p-4 align-middle">
                      <div className="text-sm font-medium text-[var(--text-secondary)]" title={wf.workflow_id}>
                        {wf.workflow_id.slice(0, 8)}...
                      </div>
                    </td>
                    <td className="p-4 align-middle">
                      <div className="text-sm font-medium text-[var(--primary)] bg-[var(--primary)]/10 px-2 py-1 rounded-md inline-block">
                        @{wf.owner_username || "unknown"}
                      </div>
                    </td>
                    <td className="p-4 align-middle">
                      <div className="flex flex-wrap gap-1">
                        {wf.tools && wf.tools.length > 0 ? (
                          wf.tools.map((tool, idx) => (
                            <span key={idx} className="px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded border border-gray-200">
                              {TOOL_MAP[tool] || tool}
                            </span>
                          ))
                        ) : (
                          <span className="text-xs text-[var(--text-secondary)] italic">Chưa xác định</span>
                        )}
                      </div>
                    </td>
                    <td className="p-4 align-middle">
                      <span
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                          wf.status === "SUCCESS"
                            ? "bg-green-500/10 text-green-500"
                            : wf.status === "FAILED"
                              ? "bg-red-500/10 text-red-500"
                              : wf.status === "CANCELLED"
                                ? "bg-gray-500/10 text-gray-500"
                                : "bg-yellow-500/10 text-yellow-500"
                        }`}
                      >
                        {wf.status === "SUCCESS" && <CheckCircle2 className="w-3.5 h-3.5" />}
                        {wf.status === "FAILED" && <AlertTriangle className="w-3.5 h-3.5" />}
                        {(wf.status === "PENDING" || wf.status === "RUNNING" || wf.status === "WAITING_APPROVAL") && (
                          <Clock className="w-3.5 h-3.5" />
                        )}
                        {wf.status}
                      </span>
                    </td>
                    <td className="p-4 align-middle">
                      <div className="text-sm text-[var(--text-secondary)] whitespace-nowrap">
                        {new Date(wf.updated_at).toLocaleString("vi-VN")}
                      </div>
                    </td>
                    <td className="p-4 align-middle text-right flex justify-end gap-2">
                      <button
                        onClick={() => toggleRow(wf.workflow_id)}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 transition-all shadow-sm"
                      >
                        {expandedRow === wf.workflow_id ? (
                          <><ChevronUp className="w-4 h-4" /> Đóng</>
                        ) : (
                          <><Eye className="w-4 h-4" /> Chi tiết</>
                        )}
                      </button>
                      {wf.status === "FAILED" && (
                        <button
                          onClick={() => handleRetry(wf.workflow_id)}
                          title="Gửi tín hiệu chạy lại"
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-[var(--primary)] text-white hover:brightness-110 transition-all shadow-sm"
                        >
                          <RotateCcw className="w-4 h-4" /> Retry
                        </button>
                      )}
                    </td>
                  </tr>
                  
                  {expandedRow === wf.workflow_id && (
                    <tr className="bg-gray-50/50 border-b border-[var(--border-light)]">
                      <td colSpan={6} className="p-6">
                        <div className="grid grid-cols-2 gap-6">
                          <div>
                            <h3 className="text-sm font-semibold text-gray-700 mb-2">Mục tiêu yêu cầu (Goal):</h3>
                            <div className="text-sm text-gray-800 bg-white p-3 rounded-lg border border-gray-200">
                              "{wf.goal}"
                            </div>
                            
                            <h3 className="text-sm font-semibold text-gray-700 mb-2 mt-4">Tiến trình (Tools):</h3>
                            <div className="flex flex-wrap gap-2 bg-white p-3 rounded-lg border border-gray-200">
                              {wf.tools && wf.tools.length > 0 ? (
                                wf.tools.map((tool, idx) => (
                                  <span key={idx} className="px-2.5 py-1 bg-gray-100 text-gray-800 text-xs rounded-md border border-gray-200 font-medium">
                                    {idx + 1}. {TOOL_MAP[tool] || tool}
                                  </span>
                                ))
                              ) : (
                                <span className="text-xs text-[var(--text-secondary)] italic">Luồng chưa gọi công cụ nào.</span>
                              )}
                            </div>

                            {wf.assistant_answer && (
                              <>
                                <h3 className="text-sm font-semibold text-gray-700 mb-2 mt-4">Phản hồi của AI (Assistant):</h3>
                                <div className="text-sm text-gray-800 bg-blue-50/50 p-3 rounded-lg border border-blue-100 leading-relaxed">
                                  {wf.assistant_answer}
                                </div>
                              </>
                            )}
                          </div>
                          
                          <div>
                            <h3 className="text-sm font-semibold text-gray-700 mb-2">Thông tin thực thi:</h3>
                            <div className="bg-white p-4 rounded-lg border border-gray-200 text-sm">
                              <div className="mb-2"><span className="font-medium">Mã luồng:</span> {wf.workflow_id}</div>
                              <div className="mb-2"><span className="font-medium">Tạo lúc:</span> {new Date(wf.created_at).toLocaleString("vi-VN")}</div>
                              <div className="mb-2"><span className="font-medium">Cập nhật lúc:</span> {new Date(wf.updated_at).toLocaleString("vi-VN")}</div>
                            </div>

                            {wf.status === "FAILED" && (
                              <div className="mt-4">
                                <h3 className="text-sm font-semibold text-red-700 mb-2">Nguyên nhân thất bại:</h3>
                                <div className="bg-red-50 p-4 rounded-lg border border-red-100 text-sm">
                                  {wf.failed_task ? (
                                    <>
                                      <div className="mb-2"><strong className="text-red-700">Nguyên nhân cụ thể:</strong> <span className="text-red-600 font-medium text-base">{wf.failed_task.message}</span></div>
                                      <div className="mb-2"><strong className="text-red-700">Tại bước (Tool):</strong> <span className="text-red-600">{TOOL_MAP[wf.failed_task.tool] || wf.failed_task.tool}</span></div>
                                      <div className="mb-3"><strong className="text-red-700">Phân loại lỗi (Mã):</strong> <span className="text-red-600">{wf.error_code ? (ERROR_CODE_MAP[wf.error_code] || wf.error_code) : "—"}</span></div>
                                      {wf.failed_task.input && (
                                        <div className="pt-2 border-t border-red-200/60">
                                          <strong className="text-red-700 block mb-1">Dữ liệu đã cung cấp (Input):</strong>
                                          <pre className="bg-white/60 p-2 rounded overflow-x-auto text-[11px] leading-relaxed text-red-900 border border-red-100 font-mono mt-1">
                                            {typeof wf.failed_task.input === 'string' 
                                              ? wf.failed_task.input 
                                              : JSON.stringify(wf.failed_task.input, null, 2)}
                                          </pre>
                                        </div>
                                      )}
                                    </>
                                  ) : (
                                    <div className="mb-2"><strong className="text-red-700">Mã lỗi:</strong> <span className="text-red-600 font-medium">{wf.error_code ? (ERROR_CODE_MAP[wf.error_code] || wf.error_code) : "—"}</span></div>
                                  )}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
