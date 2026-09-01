import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  Loader2,
  Cpu,
  ArrowRight,
  ShieldAlert,
  Clock,
  Zap,
  TrendingUp,
  Users,
  BarChart3,
  TimerReset,
} from "lucide-react";
import { Link } from "react-router-dom";

import { adminMetrics } from "../lib/agentApi";
import { usePolling } from "../lib/usePolling";

// ─── Hero Cards ───────────────────────────────────────────────────────────────
const HERO_CARDS = [
  { key: "total",   label: "Tổng luồng hệ thống", badge: "TỔNG SỐ",   Icon: Building2,   token: "var(--agent)",   spin: false, linkStatus: "" },
  { key: "running", label: "Đang xử lý",           badge: "ĐANG CHẠY", Icon: Loader2,      token: "var(--running)", spin: true,  linkStatus: "RUNNING" },
  { key: "success", label: "Thành công",            badge: "HOÀN TẤT", Icon: CheckCircle2, token: "var(--success)", spin: false, linkStatus: "SUCCESS" },
  { key: "failed",  label: "Sự cố / Thất bại",     badge: "CẦN XỬ LÝ",Icon: AlertTriangle,token: "var(--danger)",  spin: false, linkStatus: "FAILED" },
];

// ─── Lifecycle breakdown rows ─────────────────────────────────────────────────
const LIFECYCLE_ROWS = [
  { key: "running",          label: "Đang xử lý",       token: "var(--running)"      },
  { key: "waiting_approval", label: "Chờ duyệt (HITL)", token: "var(--waiting-user)" },
  { key: "awaiting_user",    label: "Chờ người dùng",    token: "var(--agent)"        },
  { key: "cancelled",        label: "Đã hủy",            token: "var(--text-muted)"   },
];

function successRateColor(r: number) {
  if (r >= 90) return "var(--success)";
  if (r >= 70) return "var(--waiting-user)";
  return "var(--danger)";
}
function successRateLabel(r: number) {
  if (r >= 90) return "Khỏe";
  if (r >= 70) return "Cần chú ý";
  return "Nguy hiểm";
}

export function AdminDashboardPage() {
  const { data, loading, error } = usePolling(adminMetrics, 10_000);

  // Derived
  const total     = data?.total    ?? 0;
  const failed    = data?.failed   ?? 0;
  const success   = data?.success  ?? 0;
  const cancelled = data?.cancelled ?? 0;
  const waiting   = data?.waiting_approval ?? 0;
  const orphaned  = (data as any)?.orphaned ?? 0;

  const denominator = success + failed + cancelled;
  const successRate = denominator > 0 ? Math.round((success / denominator) * 100) : null;
  const rateColor   = successRate !== null ? successRateColor(successRate) : "var(--text-muted)";

  const tokensPerWf = total > 0 ? Math.round((data?.llm_tokens ?? 0) / total) : null;
  const callsPerWf  = total > 0 ? ((data?.llm_calls ?? 0) / total).toFixed(1)  : null;
  const latencyS    = data?.avg_latency_ms ? (data.avg_latency_ms / 1000).toFixed(1) : null;

  // Alert
  const hasAlert   = failed > 0 || orphaned > 0 || waiting > 0;
  const alertParts: string[] = [];
  if (failed > 0)   alertParts.push(`${failed} luồng thất bại`);
  if (orphaned > 0) alertParts.push(`${orphaned} luồng treo (orphaned)`);
  if (waiting > 0)  alertParts.push(`${waiting} luồng chờ duyệt HITL`);

  // Lifecycle max for proportional bars
  const lifecycleMax = Math.max(
    data?.running ?? 0, data?.waiting_approval ?? 0,
    data?.awaiting_user ?? 0, data?.cancelled ?? 0, 1
  );

  return (
    <div className="space-y-7">
      {/* Header */}
      <div>
        <p className="font-mono text-[12px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
          BẢNG ĐIỀU KHIỂN QUẢN TRỊ
        </p>
        <h1 className="mt-2 text-[32px] sm:text-[38px] font-semibold leading-[1.12] tracking-[-0.03em] text-[var(--text-primary)]">
          Tổng quan Vận hành
        </h1>
        <p className="mt-2 text-[14.5px] text-[var(--text-secondary)]">
          Theo dõi sức khỏe hệ thống và hiệu suất Agent AI theo thời gian thực.
        </p>
      </div>

      {/* Backend error */}
      {error && (
        <div
          role="alert"
          className="flex items-center gap-3 rounded-[var(--r-sm)] p-4 text-[14px]"
          style={{
            color: "var(--danger)",
            backgroundColor: "color-mix(in srgb, var(--danger) 11%, transparent)",
            border: "1px solid color-mix(in srgb, var(--danger) 25%, transparent)",
          }}
        >
          <ShieldAlert className="h-5 w-5 shrink-0" />
          <span>Không thể tải dữ liệu chỉ số. Vui lòng kiểm tra kết nối với máy chủ backend!</span>
        </div>
      )}

      {/* ── Alert Banner ── */}
      {!loading && hasAlert && (
        <div
          className="flex items-start gap-3 rounded-[var(--r-sm)] p-4 text-[14px]"
          style={{
            color: "var(--danger)",
            backgroundColor: "color-mix(in srgb, var(--danger) 9%, transparent)",
            border: "1px solid color-mix(in srgb, var(--danger) 22%, transparent)",
          }}
        >
          <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Hệ thống cần chú ý</p>
            <p className="text-[13px] mt-0.5 opacity-80">
              {alertParts.join(" · ")}.{" "}
              <Link
                to="/admin/workflows"
                className="underline underline-offset-2 font-medium hover:opacity-100 transition-opacity"
              >
                Xem chi tiết →
              </Link>
            </p>
          </div>
        </div>
      )}

      {/* ── 4 Hero Cards ── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {HERO_CARDS.map(({ key, label, badge, Icon, token, spin, linkStatus }) => {
          const value = loading && !data ? null : (data?.[key as keyof typeof data] ?? 0) as number;
          const isCritical = key === "failed" && (value ?? 0) > 0;
          return (
            <Link
              key={key}
              to={linkStatus ? `/admin/workflows?status=${linkStatus}` : "/admin/workflows"}
              className="group relative flex flex-col justify-between rounded-[var(--r-md)] border bg-[var(--surface-overlay)] p-5 shadow-[var(--shadow-1)] transition-all duration-[var(--t-hover)] hover:shadow-[var(--shadow-2)]"
              style={{
                borderColor: isCritical
                  ? `color-mix(in srgb, ${token} 35%, transparent)`
                  : "var(--border-subtle)",
              }}
            >
              {isCritical && (
                <span
                  className="absolute inset-x-0 top-0 h-[3px] rounded-t-[var(--r-md)]"
                  style={{ backgroundColor: token }}
                />
              )}
              <div className="flex items-center justify-between mb-4">
                <div
                  className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)]"
                  style={{
                    backgroundColor: `color-mix(in srgb, ${token} 13%, transparent)`,
                    color: token,
                  }}
                >
                  <Icon className={`h-4.5 w-4.5 ${spin && (value ?? 0) > 0 ? "animate-spin" : ""}`} strokeWidth={2} />
                </div>
                <span
                  className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.08em] px-2 py-0.5 rounded-[var(--r-xs)]"
                  style={{ backgroundColor: "var(--surface-sunken)", color: "var(--text-muted)" }}
                >
                  {badge}
                </span>
              </div>
              <div>
                <p
                  className="font-mono text-[32px] font-bold tracking-tight tabular-nums"
                  style={{ color: isCritical ? token : "var(--text-primary)" }}
                >
                  {value === null ? "—" : value.toLocaleString("vi-VN")}
                </p>
                <div className="flex items-center justify-between mt-1 text-[13px] font-medium text-[var(--text-secondary)]">
                  <span>{label}</span>
                  <ArrowRight
                    className="h-3.5 w-3.5 opacity-0 group-hover:opacity-100 transition-all transform group-hover:translate-x-0.5"
                    style={{ color: "var(--agent)" }}
                  />
                </div>
              </div>
            </Link>
          );
        })}
      </div>

      {/* ── System Health KPI ── */}
      <div className="rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] p-6 shadow-[var(--shadow-1)]">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-[15px] font-semibold text-[var(--text-primary)] tracking-[-0.01em] flex items-center gap-2">
            <TrendingUp className="h-4 w-4" style={{ color: "var(--agent)" }} />
            Sức khỏe Hệ thống
          </h2>
          <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
            TARGET ≥ 90%
          </span>
        </div>

        {loading && !data ? (
          <div className="flex items-center gap-2 text-[var(--text-muted)]">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-[13.5px]">Đang tải...</span>
          </div>
        ) : successRate === null ? (
          <p className="text-[13.5px] text-[var(--text-muted)]">Chưa có đủ dữ liệu để tính tỷ lệ.</p>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[13.5px] text-[var(--text-secondary)]">
                Tỷ lệ thành công — {success} / {denominator} luồng đã kết thúc
              </span>
              <div className="flex items-center gap-2">
                <span
                  className="font-mono text-[11px] font-semibold uppercase tracking-[0.06em] px-2 py-0.5 rounded-[var(--r-xs)]"
                  style={{
                    color: rateColor,
                    backgroundColor: `color-mix(in srgb, ${rateColor} 13%, transparent)`,
                  }}
                >
                  {successRateLabel(successRate)}
                </span>
                <span className="font-mono text-[22px] font-bold tabular-nums" style={{ color: rateColor }}>
                  {successRate}%
                </span>
              </div>
            </div>
            <div className="h-2.5 w-full rounded-full bg-[var(--surface-sunken)] overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{ width: `${successRate}%`, backgroundColor: rateColor }}
              />
            </div>
            <div className="flex justify-between text-[11px] font-mono text-[var(--text-muted)]">
              <span>0%</span>
              <span className="opacity-50">70%</span>
              <span className="opacity-50">90%</span>
              <span>100%</span>
            </div>
          </div>
        )}
      </div>

      {/* ── Bottom Two Panels ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        {/* Panel A: AI Efficiency */}
        <div className="rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] p-6 shadow-[var(--shadow-1)]">
          <div className="flex items-center justify-between mb-5 pb-3 border-b border-[var(--border-subtle)]">
            <h2 className="text-[15px] font-semibold text-[var(--text-primary)] tracking-[-0.01em] flex items-center gap-2">
              <Cpu className="h-4 w-4" style={{ color: "var(--agent)" }} />
              AI Agent Efficiency
            </h2>
            <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">PER WORKFLOW</span>
          </div>

          <div className="space-y-3.5">
            {/* Tokens/wf */}
            <div className="flex items-center justify-between rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-3">
              <div className="flex items-center gap-2.5">
                <Zap className="h-4 w-4 shrink-0" style={{ color: "var(--agent)" }} />
                <div>
                  <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">Tokens / Workflow</p>
                  <p className="text-[11.5px] text-[var(--text-secondary)] mt-0.5">
                    Tổng: {loading && !data ? "—" : (data?.llm_tokens ?? 0).toLocaleString("vi-VN")}
                  </p>
                </div>
              </div>
              <p className="font-mono text-[24px] font-bold text-[var(--text-primary)] tabular-nums">
                {loading && !data ? "—" : tokensPerWf !== null ? tokensPerWf.toLocaleString("vi-VN") : "—"}
              </p>
            </div>

            {/* Calls/wf */}
            <div className="flex items-center justify-between rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-3">
              <div className="flex items-center gap-2.5">
                <BarChart3 className="h-4 w-4 shrink-0" style={{ color: "var(--running)" }} />
                <div>
                  <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">Lần Gọi LLM / Workflow</p>
                  <p className="text-[11.5px] text-[var(--text-secondary)] mt-0.5">
                    Tổng: {loading && !data ? "—" : (data?.llm_calls ?? 0).toLocaleString("vi-VN")}
                  </p>
                </div>
              </div>
              <p className="font-mono text-[24px] font-bold text-[var(--text-primary)] tabular-nums">
                {loading && !data ? "—" : callsPerWf ?? "—"}
              </p>
            </div>

            {/* Avg Latency */}
            <div className="flex items-center justify-between rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-3">
              <div className="flex items-center gap-2.5">
                <TimerReset className="h-4 w-4 shrink-0" style={{ color: "var(--waiting-user)" }} />
                <div>
                  <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">Avg E2E Latency</p>
                  <p className="text-[11.5px] text-[var(--text-secondary)] mt-0.5">Target: &lt; 5s</p>
                </div>
              </div>
              {latencyS !== null ? (
                <p
                  className="font-mono text-[24px] font-bold tabular-nums"
                  style={{ color: parseFloat(latencyS) < 5 ? "var(--success)" : "var(--danger)" }}
                >
                  {latencyS}s
                </p>
              ) : (
                <p className="font-mono text-[18px] font-bold text-[var(--text-muted)]">N/A</p>
              )}
            </div>
          </div>
        </div>

        {/* Panel B: Workflow Lifecycle */}
        <div className="rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] p-6 shadow-[var(--shadow-1)]">
          <div className="flex items-center justify-between mb-5 pb-3 border-b border-[var(--border-subtle)]">
            <h2 className="text-[15px] font-semibold text-[var(--text-primary)] tracking-[-0.01em] flex items-center gap-2">
              <Clock className="h-4 w-4" style={{ color: "var(--running)" }} />
              Vòng đời Luồng
            </h2>
            <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">TRẠNG THÁI</span>
          </div>

          <div className="space-y-4">
            {LIFECYCLE_ROWS.map(({ key, label, token }) => {
              const count = loading && !data ? null : (data?.[key as keyof typeof data] ?? 0) as number;
              const pct = count !== null && lifecycleMax > 0 ? Math.round((count / lifecycleMax) * 100) : 0;
              return (
                <div key={key}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[13px] font-medium text-[var(--text-secondary)]">{label}</span>
                    <span
                      className="font-mono text-[14px] font-bold tabular-nums"
                      style={{ color: (count ?? 0) > 0 ? token : "var(--text-muted)" }}
                    >
                      {count === null ? "—" : count}
                    </span>
                  </div>
                  <div className="h-2 w-full rounded-full bg-[var(--surface-sunken)] overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{
                        width: `${pct}%`,
                        backgroundColor: (count ?? 0) > 0 ? token : "transparent",
                      }}
                    />
                  </div>
                </div>
              );
            })}

            {/* Orphaned alert */}
            {!loading && orphaned > 0 && (
              <div
                className="mt-1 flex items-center justify-between rounded-[var(--r-sm)] px-3 py-2.5 text-[13px]"
                style={{
                  color: "var(--danger)",
                  backgroundColor: "color-mix(in srgb, var(--danger) 10%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--danger) 22%, transparent)",
                }}
              >
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  <span className="font-medium">Luồng treo (Orphaned)</span>
                </div>
                <span className="font-mono font-bold">{orphaned}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Quick Actions ── */}
      <div className="flex items-center gap-3 pt-1">
        <span className="font-mono text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] whitespace-nowrap">
          ĐIỀU HƯỚNG NHANH
        </span>
        <div className="flex-1 h-px bg-[var(--border-subtle)]" />
        <Link
          to="/admin/workflows"
          className="press inline-flex items-center gap-2 px-4 py-2 rounded-[var(--r-sm)] text-[13px] font-medium border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-primary)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-raised)] transition-all whitespace-nowrap"
        >
          <BarChart3 className="h-4 w-4" style={{ color: "var(--agent)" }} />
          Xem Lịch sử Luồng
          <ArrowRight className="h-3.5 w-3.5 text-[var(--text-muted)]" />
        </Link>
        <Link
          to="/admin/users"
          className="press inline-flex items-center gap-2 px-4 py-2 rounded-[var(--r-sm)] text-[13px] font-medium border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-primary)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-raised)] transition-all whitespace-nowrap"
        >
          <Users className="h-4 w-4" style={{ color: "var(--running)" }} />
          Quản lý Tài khoản
          <ArrowRight className="h-3.5 w-3.5 text-[var(--text-muted)]" />
        </Link>
      </div>
    </div>
  );
}
