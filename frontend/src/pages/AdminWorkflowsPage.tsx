import React, { useState, useEffect, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import {
  adminWorkflowsHistory,
  adminRetryWorkflow,
  type AdminWorkflowHistoryItem,
} from "../lib/agentApi";
import { useToast } from "../lib/toast";
import {
  RotateCcw,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Search,
  Eye,
  ChevronUp,
  XCircle,
  Loader2,
  ChevronLeft,
  ChevronRight,
  CalendarDays,
  X,
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
  NO_AVAILABILITY: "Hết chỗ / Không khả dụng",
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
  register_resident: "Đăng ký cư dân",
  register_vehicle: "Đăng ký xe",
  book_parking: "Đặt chỗ đỗ xe",
  pay_fee: "Thanh toán phí",
  search_properties: "Tìm kiếm BĐS",
  schedule_property_viewing: "Đặt lịch xem nhà",
  create_maintenance_request: "Tạo bảo trì",
  schedule_move: "Lịch chuyển nhà",
  register_property_interest: "Đăng ký quan tâm BĐS",
  book_shuttle: "Đặt xe đưa đón",
};

const STATUS_MAP: Record<string, { label: string; token: string }> = {
  SUCCESS:          { label: "HOÀN TẤT",  token: "var(--success)"      },
  RUNNING:          { label: "ĐANG CHẠY", token: "var(--running)"      },
  PENDING:          { label: "CHỜ XỬ LÝ", token: "var(--waiting-user)" },
  WAITING_APPROVAL: { label: "CHỜ DUYỆT", token: "var(--waiting-user)" },
  FAILED:           { label: "THẤT BẠI",  token: "var(--danger)"       },
  CANCELLED:        { label: "ĐÃ HỦY",    token: "var(--text-muted)"   },
};

const STATUS_FILTERS = [
  { value: "",                 label: "Tất cả"      },
  { value: "FAILED",           label: "Thất bại"    },
  { value: "SUCCESS",          label: "Hoàn tất"    },
  { value: "PENDING",          label: "Chờ xử lý"   },
  { value: "WAITING_APPROVAL", label: "Chờ duyệt"   },
];

const DATE_PRESETS = [
  { label: "Hôm nay",    days: 0  },
  { label: "7 ngày",     days: 7  },
  { label: "30 ngày",    days: 30 },
  { label: "90 ngày",    days: 90 },
];

const LIMIT = 20;

export function AdminWorkflowsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useToast();

  // ── DRAFT filters — what the user is currently selecting ─────────────
  const [draftSearch,  setDraftSearch]  = useState(searchParams.get("search_user") ?? "");
  const [draftStatus,  setDraftStatus]  = useState(searchParams.get("status")      ?? "");
  const [draftFrom,    setDraftFrom]    = useState(searchParams.get("date_from")   ?? "");
  const [draftTo,      setDraftTo]      = useState(searchParams.get("date_to")     ?? "");

  // ── APPLIED filters — what the API actually uses ──────────────────────
  const [appliedSearch, setAppliedSearch] = useState(searchParams.get("search_user") ?? "");
  const [appliedStatus, setAppliedStatus] = useState(searchParams.get("status")      ?? "");
  const [appliedFrom,   setAppliedFrom]   = useState(searchParams.get("date_from")   ?? "");
  const [appliedTo,     setAppliedTo]     = useState(searchParams.get("date_to")     ?? "");

  const [page,          setPage]          = useState(1);

  // ── Data state ────────────────────────────────────────────────────────
  const [workflows,   setWorkflows]   = useState<AdminWorkflowHistoryItem[]>([]);
  const [total,       setTotal]       = useState(0);
  const [loading,     setLoading]     = useState(true);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  // Draft differs from applied → show "pending" state on button
  const hasDraft = (
    draftSearch !== appliedSearch ||
    draftStatus !== appliedStatus ||
    draftFrom   !== appliedFrom   ||
    draftTo     !== appliedTo
  );

  const hasAppliedFilters = !!(appliedSearch || appliedStatus || appliedFrom || appliedTo);

  // ── Sync applied filters → URL ────────────────────────────────────────
  useEffect(() => {
    const p: Record<string, string> = {};
    if (appliedSearch) p.search_user = appliedSearch;
    if (appliedStatus) p.status      = appliedStatus;
    if (appliedFrom)   p.date_from   = appliedFrom;
    if (appliedTo)     p.date_to     = appliedTo;
    setSearchParams(p, { replace: true });
  }, [appliedSearch, appliedStatus, appliedFrom, appliedTo]);

  // ── Fetch — only runs when applied filters or page change ─────────────
  const fetchWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const data = await adminWorkflowsHistory(
        page, LIMIT,
        appliedSearch || undefined,
        appliedStatus || undefined,
        appliedFrom   || undefined,
        appliedTo     || undefined,
      );
      setWorkflows(data.items);
      setTotal(data.total);
    } catch (error: any) {
      toast.push("error", error.message || "Lỗi khi tải lịch sử luồng");
    } finally {
      setLoading(false);
    }
  }, [page, appliedSearch, appliedStatus, appliedFrom, appliedTo]);

  useEffect(() => { fetchWorkflows(); }, [fetchWorkflows]);

  // ── Apply: commit draft → applied, reset to page 1 ───────────────────
  const applyFilters = () => {
    setAppliedSearch(draftSearch);
    setAppliedStatus(draftStatus);
    setAppliedFrom(draftFrom);
    setAppliedTo(draftTo);
    setPage(1);
  };

  // ── Clear: reset everything ───────────────────────────────────────────
  const clearFilters = () => {
    setDraftSearch(""); setDraftStatus(""); setDraftFrom(""); setDraftTo("");
    setAppliedSearch(""); setAppliedStatus(""); setAppliedFrom(""); setAppliedTo("");
    setPage(1);
  };

  const handleRetry = async (workflowId: string) => {
    try {
      await adminRetryWorkflow(workflowId);
      toast.push("success", "Đã gửi yêu cầu chạy lại luồng");
      fetchWorkflows();
    } catch (error: any) {
      toast.push("error", error.message || "Lỗi khi yêu cầu chạy lại");
    }
  };

  const toggleRow = (id: string) => setExpandedRow(expandedRow === id ? null : id);

  const applyDatePreset = (days: number) => {
    const now = new Date();
    const to  = now.toISOString();
    const from = days === 0
      ? new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString()
      : new Date(Date.now() - days * 86400_000).toISOString();
    setDraftFrom(from);
    setDraftTo(to);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <p className="font-mono text-[12px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
          NHẬT KÝ VẬN HÀNH
        </p>
        <h1 className="mt-2 text-[32px] sm:text-[38px] font-semibold leading-[1.12] tracking-[-0.03em] text-[var(--text-primary)]">
          Lịch sử Luồng Hệ thống
        </h1>
        <p className="mt-2 text-[14.5px] text-[var(--text-secondary)]">
          Giám sát, kiểm tra chi tiết các chuỗi tác vụ và cơ chế tự phục hồi lỗi (Self-Healing).
        </p>
      </div>

      {/* ── Filter Toolbar ── */}
      <div className="rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] shadow-[var(--shadow-1)] p-4 space-y-3.5">

        {/* Row 1: Status pills */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] mr-1 whitespace-nowrap">
            Trạng thái:
          </span>
          {STATUS_FILTERS.map(({ value, label }) => {
            const active = draftStatus === value;
            const meta   = value ? STATUS_MAP[value] : null;
            return (
              <button
                key={value}
                type="button"
                onClick={() => setDraftStatus(value)}
                className="press inline-flex items-center gap-1.5 px-3 py-1.5 rounded-[var(--r-sm)] text-[12.5px] font-medium transition-all whitespace-nowrap"
                style={
                  active
                    ? {
                        backgroundColor: meta ? `color-mix(in srgb, ${meta.token} 15%, transparent)` : "var(--surface-sunken)",
                        color:           meta ? meta.token : "var(--text-primary)",
                        border:          `1px solid ${meta ? `color-mix(in srgb, ${meta.token} 35%, transparent)` : "var(--border-strong)"}`,
                        fontWeight:      600,
                      }
                    : {
                        backgroundColor: "var(--surface-raised)",
                        color:           "var(--text-secondary)",
                        border:          "1px solid var(--border-subtle)",
                      }
                }
              >
                {active && meta && (
                  <span
                    className="inline-block h-1.5 w-1.5 rounded-full shrink-0"
                    style={{ backgroundColor: meta.token }}
                  />
                )}
                {label}
              </button>
            );
          })}
        </div>

        {/* Row 2: Date presets + custom range */}
        <div className="flex items-center gap-2 flex-wrap">
          <CalendarDays className="h-3.5 w-3.5 text-[var(--text-muted)] shrink-0" />
          <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] mr-1 whitespace-nowrap">
            Thời gian:
          </span>
          {DATE_PRESETS.map(({ label, days }) => (
            <button
              key={label}
              type="button"
              onClick={() => applyDatePreset(days)}
              className="press px-2.5 py-1 rounded-[var(--r-sm)] text-[12px] font-medium border border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--text-secondary)] hover:border-[var(--agent)] hover:text-[var(--agent)] transition-all whitespace-nowrap"
            >
              {label}
            </button>
          ))}

          {/* Custom range */}
          <input
            type="date"
            value={draftFrom ? draftFrom.slice(0, 10) : ""}
            onChange={(e) => setDraftFrom(e.target.value ? new Date(e.target.value).toISOString() : "")}
            className="h-8 px-2 text-[12px] rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-primary)] outline-none focus:border-[var(--agent)] transition-colors"
          />
          <span className="text-[var(--text-muted)] text-[12px]">→</span>
          <input
            type="date"
            value={draftTo ? draftTo.slice(0, 10) : ""}
            onChange={(e) => setDraftTo(e.target.value ? new Date(e.target.value + "T23:59:59").toISOString() : "")}
            className="h-8 px-2 text-[12px] rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-primary)] outline-none focus:border-[var(--agent)] transition-colors"
          />
        </div>

        {/* Row 3: Search + actions */}
        <div className="flex items-center gap-2 flex-wrap border-t border-[var(--border-subtle)] pt-3.5">
          {/* User search */}
          <div className="relative flex-1 min-w-[180px] max-w-[280px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-muted)]" />
            <input
              type="text"
              placeholder="Tìm theo username..."
              value={draftSearch}
              onChange={(e) => setDraftSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applyFilters()}
              className="h-9 w-full pl-9 pr-3 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[13px] text-[var(--text-primary)] placeholder-[var(--text-muted)] outline-none transition-colors focus:border-[var(--agent)]"
            />
          </div>

          <div className="flex-1" />

          {/* Clear filters — only show if anything is applied */}
          {(hasAppliedFilters || draftSearch || draftStatus || draftFrom || draftTo) && (
            <button
              type="button"
              onClick={clearFilters}
              className="press inline-flex items-center gap-1.5 px-3 py-2 rounded-[var(--r-sm)] text-[12.5px] font-medium border border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--text-secondary)] hover:text-[var(--danger)] hover:border-[var(--danger)] transition-all whitespace-nowrap"
            >
              <X className="h-3.5 w-3.5" /> Xóa bộ lọc
            </button>
          )}

          {/* Result count badge */}
          <span className="font-mono text-[12px] font-semibold px-3 py-2 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--text-muted)] whitespace-nowrap">
            {loading ? "…" : `${total.toLocaleString("vi-VN")} kết quả`}
          </span>

          {/* ── APPLY BUTTON ── */}
          <button
            type="button"
            onClick={applyFilters}
            disabled={loading}
            className="press inline-flex items-center gap-2 px-5 py-2 rounded-[var(--r-sm)] text-[13px] font-semibold transition-all whitespace-nowrap disabled:opacity-60"
            style={
              hasDraft
                ? {
                    backgroundColor: "var(--agent)",
                    color: "#fff",
                    border: "none",
                    boxShadow: `0 0 0 3px color-mix(in srgb, var(--agent) 25%, transparent)`,
                  }
                : {
                    backgroundColor: "var(--agent)",
                    color: "#fff",
                    border: "none",
                    opacity: 0.85,
                  }
            }
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Search className="h-3.5 w-3.5" />
            )}
            {hasDraft ? "Áp dụng bộ lọc" : "Tải lại"}
          </button>
        </div>
      </div>

      {/* Main Table Card */}
      <div className="rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] shadow-[var(--shadow-1)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed text-left border-collapse min-w-[1080px]">
            <colgroup>
              <col style={{ width: "100px" }} />
              <col style={{ width: "130px" }} />
              <col style={{ width: "auto" }} />
              <col style={{ width: "140px" }} />
              <col style={{ width: "170px" }} />
              <col style={{ width: "180px" }} />
            </colgroup>
            <thead>
              <tr className="bg-[var(--surface-raised)] border-b border-[var(--border-subtle)]">
                <th className="py-3 px-4 font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] whitespace-nowrap">
                  Mã Luồng
                </th>
                <th className="py-3 px-4 font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] whitespace-nowrap">
                  Người dùng
                </th>
                <th className="py-3 px-4 font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] whitespace-nowrap">
                  Chuỗi Tác vụ (Task DAG)
                </th>
                <th className="py-3 px-4 font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] whitespace-nowrap">
                  Trạng thái
                </th>
                <th className="py-3 px-4 font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] whitespace-nowrap">
                  Thời gian
                </th>
                <th className="py-3 px-4 font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] text-right whitespace-nowrap">
                  Thao tác
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)]">
              {loading && workflows.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-[14px] text-[var(--text-muted)]">
                    <div className="flex flex-col items-center gap-2">
                      <Loader2 className="h-6 w-6 animate-spin text-[var(--agent)]" />
                      <span>Đang đồng bộ dữ liệu luồng...</span>
                    </div>
                  </td>
                </tr>
              )}
              {workflows.map((wf) => {
                const statusMeta = STATUS_MAP[wf.status] || { label: wf.status, token: "var(--text-muted)" };
                return (
                  <React.Fragment key={wf.workflow_id}>
                    <tr className={`hover:bg-[var(--surface-raised)] transition-colors duration-[var(--t-hover)] ${expandedRow === wf.workflow_id ? 'bg-[var(--surface-raised)]' : ''}`}>
                      <td className="py-3.5 px-4 align-middle font-mono text-[12.5px] font-semibold text-[var(--text-primary)] whitespace-nowrap truncate">
                        #{wf.workflow_id.slice(0, 8)}
                      </td>
                      <td className="py-3.5 px-4 align-middle whitespace-nowrap">
                        <span className="inline-block max-w-[110px] truncate font-mono text-[12px] font-medium px-2 py-0.5 rounded-[var(--r-xs)] bg-[var(--surface-sunken)] text-[var(--text-primary)]" title={wf.owner_username || undefined}>
                          @{wf.owner_username || "anonymous"}
                        </span>
                      </td>
                      <td className="py-3.5 px-4 align-middle overflow-hidden">
                        <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5">
                          {wf.tools && wf.tools.length > 0 ? (
                            wf.tools.map((tool, idx) => (
                              <React.Fragment key={idx}>
                                <span
                                  className="whitespace-nowrap shrink-0 font-mono text-[11px] font-medium px-2 py-0.5 rounded-[var(--r-xs)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--text-secondary)]"
                                >
                                  {TOOL_MAP[tool] || tool}
                                </span>
                                {idx < wf.tools.length - 1 && (
                                  <span className="shrink-0 text-[10px] text-[var(--text-muted)] font-mono select-none">
                                    ➔
                                  </span>
                                )}
                              </React.Fragment>
                            ))
                          ) : (
                            <span className="text-[12px] text-[var(--text-muted)] italic whitespace-nowrap">Chưa gọi công cụ</span>
                          )}
                        </div>
                      </td>
                      <td className="py-3.5 px-4 align-middle whitespace-nowrap">
                        <span
                          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-[var(--r-xs)] font-mono text-[11px] font-semibold uppercase tracking-[0.05em] whitespace-nowrap"
                          style={{
                            color: statusMeta.token,
                            backgroundColor: `color-mix(in srgb, ${statusMeta.token} 12%, transparent)`,
                          }}
                        >
                          {wf.status === "SUCCESS" && <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />}
                          {wf.status === "FAILED" && <AlertTriangle className="w-3.5 h-3.5 shrink-0" />}
                          {(wf.status === "PENDING" || wf.status === "RUNNING" || wf.status === "WAITING_APPROVAL") && (
                            <Clock className="w-3.5 h-3.5 shrink-0" />
                          )}
                          {wf.status === "CANCELLED" && <XCircle className="w-3.5 h-3.5 shrink-0" />}
                          {statusMeta.label}
                        </span>
                      </td>
                      <td className="py-3.5 px-4 align-middle whitespace-nowrap font-mono text-[12px] text-[var(--text-muted)]">
                        {new Date(wf.updated_at).toLocaleString("vi-VN")}
                      </td>
                      <td className="py-3.5 px-4 align-middle text-right whitespace-nowrap">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            type="button"
                            onClick={() => toggleRow(wf.workflow_id)}
                            className="press inline-flex items-center gap-1 px-2.5 py-1.5 rounded-[var(--r-sm)] text-[12px] font-medium border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-primary)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-raised)] transition-all whitespace-nowrap"
                          >
                            {expandedRow === wf.workflow_id ? (
                              <><ChevronUp className="w-3.5 h-3.5" /> Đóng</>
                            ) : (
                              <><Eye className="w-3.5 h-3.5" style={{ color: "var(--agent)" }} /> Chi tiết</>
                            )}
                          </button>
                          {wf.status === "FAILED" && (
                            <button
                              type="button"
                              onClick={() => handleRetry(wf.workflow_id)}
                              title="Gửi tín hiệu chạy lại luồng"
                              className="press inline-flex items-center gap-1 px-2.5 py-1.5 rounded-[var(--r-sm)] text-[12px] font-semibold text-white bg-[var(--agent)] hover:opacity-90 transition-opacity whitespace-nowrap"
                            >
                              <RotateCcw className="w-3.5 h-3.5" /> Retry
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    
                    {expandedRow === wf.workflow_id && (
                      <tr className="bg-[var(--surface-raised)] border-b border-[var(--border-subtle)]">
                        <td colSpan={6} className="p-6">
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 items-start">
                            <div className="space-y-4">
                              <div>
                                <h3 className="font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] mb-1.5">
                                  Mục tiêu người dùng (Goal):
                                </h3>
                                <div className="text-[14px] font-medium text-[var(--text-primary)] bg-[var(--surface-overlay)] p-3.5 rounded-[var(--r-sm)] border border-[var(--border-subtle)] leading-relaxed">
                                  "{wf.goal}"
                                </div>
                              </div>
                              
                              <div>
                                <h3 className="font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] mb-1.5">
                                  Chuỗi công cụ thực thi:
                                </h3>
                                <div className="flex flex-wrap gap-2 bg-[var(--surface-overlay)] p-3.5 rounded-[var(--r-sm)] border border-[var(--border-subtle)]">
                                  {wf.tools && wf.tools.length > 0 ? (
                                    wf.tools.map((tool, idx) => (
                                      <span
                                        key={idx}
                                        className="font-mono text-[12px] px-2.5 py-1 bg-[var(--surface-raised)] text-[var(--text-primary)] rounded-[var(--r-xs)] border border-[var(--border-subtle)] font-medium"
                                      >
                                        {idx + 1}. {TOOL_MAP[tool] || tool}
                                      </span>
                                    ))
                                  ) : (
                                    <span className="text-[12.5px] text-[var(--text-muted)] italic">Luồng chưa kích hoạt công cụ nào.</span>
                                  )}
                                </div>
                              </div>

                              {wf.assistant_answer && (
                                <div>
                                  <h3 className="font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] mb-1.5">
                                    Phản hồi của AI Agent:
                                  </h3>
                                  <div
                                    className="text-[13.5px] text-[var(--text-primary)] p-3.5 rounded-[var(--r-sm)] border border-[var(--border-subtle)] leading-relaxed"
                                    style={{ backgroundColor: "color-mix(in srgb, var(--agent) 8%, transparent)" }}
                                  >
                                    {wf.assistant_answer}
                                  </div>
                                </div>
                              )}
                            </div>
                            
                            <div className="space-y-4">
                              <div>
                                <h3 className="font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] mb-1.5">
                                  Dữ liệu thực thi hệ thống:
                                </h3>
                                <div className="bg-[var(--surface-overlay)] p-4 rounded-[var(--r-sm)] border border-[var(--border-subtle)] text-[12.5px] font-mono space-y-2">
                                  <div className="flex justify-between items-center"><span className="text-[var(--text-muted)]">Mã Luồng (ID):</span> <span className="font-semibold text-[var(--text-primary)] select-all">{wf.workflow_id}</span></div>
                                  <div className="flex justify-between items-center"><span className="text-[var(--text-muted)]">Khởi tạo lúc:</span> <span className="text-[var(--text-primary)]">{new Date(wf.created_at).toLocaleString("vi-VN")}</span></div>
                                  <div className="flex justify-between items-center"><span className="text-[var(--text-muted)]">Cập nhật lúc:</span> <span className="text-[var(--text-primary)]">{new Date(wf.updated_at).toLocaleString("vi-VN")}</span></div>
                                </div>
                              </div>

                              {wf.status === "FAILED" && (
                                <div>
                                  <h3 className="font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--danger)] mb-1.5 flex items-center gap-1.5">
                                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                                    Báo cáo Sự cố & Lỗi Thực thi:
                                  </h3>
                                  <div
                                    className="p-4 rounded-[var(--r-sm)] text-[12.5px] space-y-2 border"
                                    style={{
                                      backgroundColor: "color-mix(in srgb, var(--danger) 8%, transparent)",
                                      borderColor: "color-mix(in srgb, var(--danger) 25%, transparent)",
                                    }}
                                  >
                                    {wf.failed_task ? (
                                      <>
                                        <div><span className="font-semibold" style={{ color: "var(--danger)" }}>Thông điệp:</span> <span className="text-[var(--text-primary)] font-medium text-[13px] ml-1">{wf.failed_task.message}</span></div>
                                        <div><span className="font-semibold" style={{ color: "var(--danger)" }}>Tại Bước:</span> <span className="font-mono text-[var(--text-primary)] ml-1">{TOOL_MAP[wf.failed_task.tool] || wf.failed_task.tool}</span></div>
                                        <div><span className="font-semibold" style={{ color: "var(--danger)" }}>Mã Lỗi:</span> <span className="font-mono font-semibold ml-1" style={{ color: "var(--danger)" }}>{wf.error_code ? (ERROR_CODE_MAP[wf.error_code] || wf.error_code) : "—"}</span></div>
                                        {wf.failed_task.input && (
                                          <div className="pt-2 border-t border-[var(--border-subtle)]">
                                            <span className="font-semibold block mb-1" style={{ color: "var(--danger)" }}>Dữ liệu Đầu vào (Input):</span>
                                            <pre className="bg-[var(--surface-overlay)] p-2.5 rounded-[var(--r-xs)] overflow-x-auto text-[11px] leading-relaxed text-[var(--text-primary)] border border-[var(--border-subtle)] font-mono">
                                              {typeof wf.failed_task.input === 'string' 
                                                ? wf.failed_task.input 
                                                : JSON.stringify(wf.failed_task.input, null, 2)}
                                            </pre>
                                          </div>
                                        )}
                                      </>
                                    ) : (
                                      <div><span className="font-semibold" style={{ color: "var(--danger)" }}>Mã Lỗi:</span> <span className="font-mono font-semibold ml-1" style={{ color: "var(--danger)" }}>{wf.error_code ? (ERROR_CODE_MAP[wf.error_code] || wf.error_code) : "—"}</span></div>
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
                );
              })}
              {workflows.length === 0 && !loading && (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-[14px] text-[var(--text-muted)]">
                    Không có luồng xử lý nào được tìm thấy.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ── Pagination Footer ── */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-[var(--border-subtle)] bg-[var(--surface-raised)]">
            {/* Range info */}
            <p className="font-mono text-[12px] text-[var(--text-muted)]">
              {loading ? "…" : `${(page - 1) * LIMIT + 1}–${Math.min(page * LIMIT, total)} / ${total} luồng`}
            </p>

            {/* Controls */}
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1 || loading}
                className="press flex items-center justify-center h-8 w-8 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-secondary)] hover:border-[var(--border-strong)] disabled:opacity-40 disabled:pointer-events-none transition-all"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>

              {/* Page number pills — show up to 7 */}
              {Array.from({ length: totalPages }, (_, i) => i + 1)
                .filter(n => {
                  if (totalPages <= 7) return true;
                  if (n === 1 || n === totalPages) return true;
                  return Math.abs(n - page) <= 2;
                })
                .reduce<(number | "…")[]>((acc, n, idx, arr) => {
                  if (idx > 0 && typeof arr[idx - 1] === "number" && (n as number) - (arr[idx - 1] as number) > 1) {
                    acc.push("…");
                  }
                  acc.push(n);
                  return acc;
                }, [])
                .map((n, idx) =>
                  n === "…" ? (
                    <span key={`ellipsis-${idx}`} className="w-8 text-center text-[var(--text-muted)] text-[13px]">…</span>
                  ) : (
                    <button
                      key={n}
                      type="button"
                      onClick={() => setPage(n as number)}
                      disabled={loading}
                      className="press flex items-center justify-center h-8 w-8 rounded-[var(--r-sm)] font-mono text-[12px] font-semibold transition-all disabled:opacity-50"
                      style={
                        page === n
                          ? { backgroundColor: "var(--agent)", color: "#fff", border: "none" }
                          : { border: "1px solid var(--border-subtle)", backgroundColor: "var(--surface-overlay)", color: "var(--text-secondary)" }
                      }
                    >
                      {n}
                    </button>
                  )
                )
              }

              <button
                type="button"
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page === totalPages || loading}
                className="press flex items-center justify-center h-8 w-8 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-secondary)] hover:border-[var(--border-strong)] disabled:opacity-40 disabled:pointer-events-none transition-all"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>

            {/* Per-page hint */}
            <p className="font-mono text-[12px] text-[var(--text-muted)]">
              {LIMIT} / trang
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

