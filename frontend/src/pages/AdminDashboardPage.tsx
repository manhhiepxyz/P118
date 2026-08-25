import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  Loader2,
  Cpu,
  ArrowRight,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

import { adminMetrics } from "../lib/agentApi";
import { usePolling } from "../lib/usePolling";

const COLORS = {
  success: "#22c55e",
  running: "#3b82f6",
  waiting_approval: "#f59e0b",
  failed: "#ef4444",
  cancelled: "#6b7280",
  awaiting_user: "#8b5cf6",
};

const CARDS = [
  {
    key: "total",
    label: "Tổng luồng",
    Icon: Building2,
    color: "text-blue-500",
    bg: "bg-blue-500/10",
  },
  {
    key: "running",
    label: "Đang xử lý",
    Icon: Loader2,
    color: "text-amber-500",
    bg: "bg-amber-500/10",
  },
  {
    key: "success",
    label: "Hoàn tất",
    Icon: CheckCircle2,
    color: "text-green-500",
    bg: "bg-green-500/10",
  },
  {
    key: "failed",
    label: "Thất bại",
    Icon: AlertTriangle,
    color: "text-red-500",
    bg: "bg-red-500/10",
  },
];

export function AdminDashboardPage() {
  const { data, loading, error } = usePolling(adminMetrics, 10000);

  const pieData = data
    ? [
        { name: "Hoàn tất", value: data.success, color: COLORS.success },
        { name: "Đang chạy", value: data.running, color: COLORS.running },
        {
          name: "Chờ xác nhận",
          value: data.waiting_approval,
          color: COLORS.waiting_approval,
        },
        { name: "Thất bại", value: data.failed, color: COLORS.failed },
        { name: "Đã hủy", value: data.cancelled, color: COLORS.cancelled },
        { name: "Chờ khách", value: data.awaiting_user, color: COLORS.awaiting_user },
      ].filter((item) => item.value > 0)
    : [];

  const barData = data
    ? [
        {
          name: "LLM Usage",
          Tokens: data.llm_tokens,
          Calls: data.llm_calls,
        },
      ]
    : [];

  return (
    <div className="h-full overflow-y-auto p-8">
      <div className="mx-auto max-w-6xl">
        <div className="flex items-center gap-4 mb-8">
          <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-blue-500/10 border border-blue-500/20 text-blue-500 shadow-inner">
            <Building2 className="w-6 h-6" strokeWidth={2} />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[var(--text-primary)]">Tổng quan Vận hành</h1>
            <p className="text-sm text-[var(--text-secondary)] mt-1">Giám sát trạng thái hoạt động của các luồng xử lý và tài nguyên AI trong thời gian thực</p>
          </div>
        </div>

        {error && (
          <div className="mb-8 rounded-xl bg-red-500/10 p-4 text-red-500 border border-red-500/20">
            Không thể tải dữ liệu. Vui lòng kiểm tra kết nối!
          </div>
        )}

        {/* Cards Section */}
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4 mb-8">
          {CARDS.map(({ key, label, Icon, color, bg }) => (
            <Link
              key={key}
              to="/admin/workflows"
              className="group relative flex flex-col justify-between overflow-hidden rounded-2xl bg-[var(--surface)]/80 backdrop-blur-md border border-[var(--border-light)] p-6 shadow-sm hover:shadow-md transition-all hover:border-[var(--border-strong)]"
            >
              <div className="flex items-center justify-between mb-4">
                <div
                  className={`flex h-12 w-12 items-center justify-center rounded-xl ${bg}`}
                >
                  <Icon className={`h-6 w-6 ${color}`} strokeWidth={2} />
                </div>
                <ArrowRight className="h-5 w-5 text-[var(--text-muted)] opacity-0 group-hover:opacity-100 transition-opacity transform group-hover:translate-x-1" />
              </div>
              <div>
                <p className="text-3xl font-bold text-[var(--text-primary)] tabular-nums">
                  {loading && !data
                    ? "—"
                    : (data?.[key as keyof typeof data] ?? 0).toLocaleString("vi-VN")}
                </p>
                <p className="mt-1 text-sm font-medium text-[var(--text-secondary)]">
                  {label}
                </p>
              </div>
            </Link>
          ))}
        </div>

        {/* Charts Section */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Pie Chart: Status Distribution */}
          <div className="rounded-2xl bg-[var(--surface)]/80 backdrop-blur-md border border-[var(--border-light)] p-6 shadow-sm flex flex-col">
            <h2 className="text-lg font-bold text-[var(--text-primary)] mb-6">
              Phân bổ Trạng thái Luồng
            </h2>
            <div className="h-[250px] w-full flex-1">
              {loading && !data ? (
                <div className="flex h-full items-center justify-center">
                  <Loader2 className="h-8 w-8 animate-spin text-[var(--text-muted)]" />
                </div>
              ) : pieData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={pieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={70}
                      outerRadius={95}
                      paddingAngle={4}
                      dataKey="value"
                    >
                      {pieData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        borderRadius: "12px",
                        border: "none",
                        boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
                        backgroundColor: "var(--surface)",
                        color: "var(--text-primary)",
                      }}
                      itemStyle={{ fontWeight: 500 }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full items-center justify-center text-[var(--text-muted)]">
                  Chưa có dữ liệu
                </div>
              )}
            </div>
            {/* Custom Legend */}
            <div className="mt-4 flex flex-wrap justify-center gap-4">
              {pieData.map((entry, index) => (
                <div key={index} className="flex items-center gap-2">
                  <span
                    className="block h-3 w-3 rounded-full"
                    style={{ backgroundColor: entry.color }}
                  />
                  <span className="text-sm text-[var(--text-secondary)]">
                    {entry.name}
                  </span>
                  <span className="text-sm font-medium text-[var(--text-primary)]">
                    {entry.value.toLocaleString("vi-VN")}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Bar Chart: LLM Usage */}
          <div className="rounded-2xl bg-[var(--surface)]/80 backdrop-blur-md border border-[var(--border-light)] p-6 shadow-sm flex flex-col">
            <h2 className="text-lg font-bold text-[var(--text-primary)] mb-6 flex items-center gap-2">
              <Cpu className="h-5 w-5 text-indigo-500" /> Tiêu thụ AI (LLM)
            </h2>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
              <div className="rounded-xl bg-indigo-500/10 p-4 border border-indigo-500/20">
                <p className="text-sm font-medium text-indigo-600 mb-1">
                  Tổng Tokens
                </p>
                <p className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">
                  {loading && !data
                    ? "—"
                    : (data?.llm_tokens ?? 0).toLocaleString("vi-VN")}
                </p>
              </div>
              <div className="rounded-xl bg-purple-500/10 p-4 border border-purple-500/20">
                <p className="text-sm font-medium text-purple-600 mb-1">
                  Tổng Calls
                </p>
                <p className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">
                  {loading && !data
                    ? "—"
                    : (data?.llm_calls ?? 0).toLocaleString("vi-VN")}
                </p>
              </div>
              <div className="rounded-xl bg-emerald-500/10 p-4 border border-emerald-500/20">
                <p className="text-sm font-medium text-emerald-600 mb-1">
                  Chi phí ($)
                </p>
                <p className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">
                  {loading && !data
                    ? "—"
                    : `$${(data?.total_cost ?? 0).toFixed(4)}`}
                </p>
              </div>
              <div className="rounded-xl bg-rose-500/10 p-4 border border-rose-500/20">
                <p className="text-sm font-medium text-rose-600 mb-1">
                  Trễ TB (ms)
                </p>
                <p className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">
                  {loading && !data
                    ? "—"
                    : (data?.avg_latency_ms ?? 0).toFixed(0)}
                </p>
              </div>
            </div>

            <div className="flex-1 min-h-[150px] w-full mt-auto">
              {loading && !data ? (
                <div className="flex h-full items-center justify-center">
                  <Loader2 className="h-8 w-8 animate-spin text-[var(--text-muted)]" />
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={barData}
                    layout="vertical"
                    margin={{ top: 0, right: 0, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      horizontal={false}
                      vertical={true}
                      stroke="var(--border-subtle)"
                    />
                    <XAxis type="number" hide />
                    <YAxis dataKey="name" type="category" hide />
                    <Tooltip
                      cursor={{ fill: "var(--surface-hover)" }}
                      contentStyle={{
                        borderRadius: "12px",
                        border: "none",
                        boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
                        backgroundColor: "var(--surface)",
                        color: "var(--text-primary)",
                      }}
                    />
                    <Bar
                      dataKey="Tokens"
                      fill="#6366f1"
                      radius={[0, 4, 4, 0]}
                      barSize={20}
                    />
                    <Bar
                      dataKey="Calls"
                      fill="#a855f7"
                      radius={[0, 4, 4, 0]}
                      barSize={20}
                    />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
