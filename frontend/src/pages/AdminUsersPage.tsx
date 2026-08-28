import { useState, useEffect } from "react";
import {
  adminListUsers,
  adminUpdateUserRole,
  adminUpdateUserStatus,
  type AdminUser,
} from "../lib/agentApi";
import { useToast } from "../lib/toast";
import { Lock, Unlock, Search, Loader2, Info, X } from "lucide-react";

export function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedUser, setSelectedUser] = useState<AdminUser | null>(null);
  const toast = useToast();

  const fetchUsers = async () => {
    try {
      const data = await adminListUsers();
      setUsers(data.items);
    } catch (error: any) {
      toast.push("error", error.message || "Lỗi khi tải danh sách người dùng");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const handleRoleChange = async (
    userId: string,
    newRole: "customer" | "admin" | "provider",
  ) => {
    try {
      await adminUpdateUserRole(userId, newRole);
      toast.push("success", "Đã cập nhật vai trò người dùng thành công");
      fetchUsers();
    } catch (error: any) {
      toast.push("error", error.message || "Cập nhật vai trò thất bại");
    }
  };

  const handleStatusChange = async (userId: string, isArchived: boolean) => {
    try {
      await adminUpdateUserStatus(userId, isArchived);
      toast.push("success", isArchived ? "Đã khóa truy cập tài khoản" : "Đã kích hoạt lại tài khoản");
      fetchUsers();
    } catch (error: any) {
      toast.push("error", error.message || "Cập nhật trạng thái thất bại");
    }
  };

  const filteredUsers = users.filter((u) => {
    const q = searchTerm.toLowerCase();
    return (
      u.username.toLowerCase().includes(q) ||
      (u.full_name && u.full_name.toLowerCase().includes(q)) ||
      (u.email && u.email.toLowerCase().includes(q)) ||
      u.id.toLowerCase().includes(q)
    );
  });

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--text-muted)]">
        <div className="flex flex-col items-center gap-2">
          <Loader2 className="h-6 w-6 animate-spin text-[var(--agent)]" />
          <span className="text-[14px] font-medium">Đang tải danh bạ người dùng...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <p className="font-mono text-[12px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
            QUẢN TRỊ TÀI KHOẢN
          </p>
          <h1 className="mt-2 text-[32px] sm:text-[38px] font-semibold leading-[1.12] tracking-[-0.03em] text-[var(--text-primary)]">
            Người dùng & Phân quyền
          </h1>
          <p className="mt-2 text-[14.5px] text-[var(--text-secondary)]">
            Kiểm soát vai trò (RBAC) và quản lý trạng thái truy cập tài khoản trên toàn hệ thống.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--text-muted)]" />
            <input
              type="text"
              placeholder="Tìm tên, email, UUID..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="h-10 w-[240px] pl-10 pr-3 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[14px] text-[var(--text-primary)] placeholder-[var(--text-muted)] outline-none transition-colors focus:border-[var(--agent)]"
            />
          </div>
          <span className="font-mono text-[12px] font-semibold px-3 py-2 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--text-secondary)]">
            {filteredUsers.length} TÀI KHOẢN
          </span>
        </div>
      </div>

      {/* Main Table Card */}
      <div className="rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] shadow-[var(--shadow-1)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-[var(--surface-raised)] border-b border-[var(--border-subtle)]">
                <th className="py-3.5 px-6 font-mono text-[12px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
                  Tài khoản & Định danh
                </th>
                <th className="py-3.5 px-6 font-mono text-[12px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
                  Họ tên & Liên hệ
                </th>
                <th className="py-3.5 px-6 font-mono text-[12px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
                  Vai trò (Role)
                </th>
                <th className="py-3.5 px-6 font-mono text-[12px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
                  Trạng thái
                </th>
                <th className="py-3.5 px-6 font-mono text-[12px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)] text-right">
                  Hành động
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)]">
              {filteredUsers.map((user) => (
                <tr
                  key={user.id}
                  className="hover:bg-[var(--surface-raised)] transition-colors duration-[var(--t-hover)]"
                >
                  <td className="py-4 px-6">
                    <div className="text-[14.5px] font-semibold text-[var(--text-primary)]">
                      {user.username}
                    </div>
                    <div className="font-mono text-[12px] text-[var(--text-muted)] mt-0.5">
                      #{user.id.slice(0, 8)}
                    </div>
                  </td>
                  <td className="py-4 px-6">
                    <div className="text-[14px] font-medium text-[var(--text-primary)]">
                      {user.full_name || "—"}
                    </div>
                    <div className="text-[12.5px] text-[var(--text-muted)] mt-0.5">
                      {user.email || user.phone || "Chưa cập nhật"}
                    </div>
                  </td>
                  <td className="py-4 px-6">
                    {user.role === "provider" ? (
                      <span
                        className="inline-flex items-center px-2.5 py-1 rounded-[var(--r-xs)] font-mono text-[11.5px] font-semibold tracking-[0.05em] uppercase"
                        style={{
                          color: "var(--running)",
                          backgroundColor: "color-mix(in srgb, var(--running) 12%, transparent)",
                        }}
                      >
                        PROVIDER
                      </span>
                    ) : (
                      <select
                        className="h-8 bg-[var(--surface-overlay)] border border-[var(--border-subtle)] rounded-[var(--r-sm)] px-2.5 py-1 text-[13px] font-medium text-[var(--text-primary)] outline-none transition-colors hover:border-[var(--border-strong)] focus:border-[var(--agent)]"
                        value={user.role}
                        onChange={(e) =>
                          handleRoleChange(user.id, e.target.value as any)
                        }
                      >
                        <option value="customer">CUSTOMER</option>
                        <option value="admin">ADMIN</option>
                      </select>
                    )}
                  </td>
                  <td className="py-4 px-6">
                    <span
                      className="inline-flex items-center px-2.5 py-1 rounded-[var(--r-xs)] font-mono text-[11.5px] font-semibold tracking-[0.05em] uppercase"
                      style={{
                        color: user.archived_at ? "var(--danger)" : "var(--success)",
                        backgroundColor: user.archived_at
                          ? "color-mix(in srgb, var(--danger) 12%, transparent)"
                          : "color-mix(in srgb, var(--success) 12%, transparent)",
                      }}
                    >
                      {user.archived_at ? "ĐÃ KHÓA" : "HOẠT ĐỘNG"}
                    </span>
                  </td>
                  <td className="py-4 px-6 text-right space-x-2">
                    <button
                      type="button"
                      onClick={() => setSelectedUser(user)}
                      title="Xem chi tiết"
                      className="press inline-flex items-center justify-center w-8 h-8 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-secondary)] hover:border-[var(--border-strong)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-raised)] transition-all"
                    >
                      <Info className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        handleStatusChange(user.id, !user.archived_at)
                      }
                      className={`press inline-flex items-center gap-1.5 px-3 py-1.5 rounded-[var(--r-sm)] text-[13px] font-medium transition-all ${
                        user.archived_at
                          ? "border border-[var(--border-subtle)] bg-[var(--surface-overlay)] text-[var(--text-primary)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-raised)]"
                          : "text-white hover:opacity-90"
                      }`}
                      style={
                        !user.archived_at
                          ? { backgroundColor: "var(--danger)" }
                          : undefined
                      }
                    >
                      {user.archived_at ? (
                        <>
                          <Unlock className="w-3.5 h-3.5" style={{ color: "var(--agent)" }} /> Mở khóa
                        </>
                      ) : (
                        <>
                          <Lock className="w-3.5 h-3.5" /> Khóa
                        </>
                      )}
                    </button>
                  </td>
                </tr>
              ))}
              {filteredUsers.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="py-12 text-center text-[14px] text-[var(--text-muted)]"
                  >
                    Không tìm thấy người dùng nào phù hợp.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      {/* Details Modal */}
      {selectedUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-[var(--surface-base)] w-full max-w-lg rounded-[var(--r-md)] border border-[var(--border-strong)] shadow-2xl flex flex-col overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--surface-raised)]">
              <h3 className="text-[16px] font-semibold text-[var(--text-primary)]">
                Chi tiết người dùng
              </h3>
              <button
                type="button"
                onClick={() => setSelectedUser(null)}
                className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-6 overflow-y-auto max-h-[70vh]">
              <div className="flex items-center gap-4 mb-6">
                <div className="w-16 h-16 rounded-full bg-[var(--surface-raised)] border border-[var(--border-subtle)] overflow-hidden flex items-center justify-center shrink-0">
                  {selectedUser.avatar_url ? (
                    <img src={selectedUser.avatar_url} alt="Avatar" className="w-full h-full object-cover" />
                  ) : (
                    <span className="text-[20px] font-medium text-[var(--text-muted)]">
                      {selectedUser.username.charAt(0).toUpperCase()}
                    </span>
                  )}
                </div>
                <div>
                  <div className="text-[18px] font-semibold text-[var(--text-primary)]">
                    {selectedUser.full_name || selectedUser.username}
                  </div>
                  <div className="text-[14px] text-[var(--text-secondary)] mt-0.5">
                    ID: {selectedUser.id}
                  </div>
                  <div className="text-[14px] text-[var(--text-secondary)] mt-0.5">
                    Tham gia: {new Date(selectedUser.created_at).toLocaleDateString("vi-VN")}
                  </div>
                </div>
              </div>

              <dl className="grid gap-y-4 gap-x-6 sm:grid-cols-2">
                {[
                  { label: "Username", value: selectedUser.username },
                  { label: "Họ và tên", value: selectedUser.full_name },
                  { label: "Email", value: selectedUser.email },
                  { label: "Số điện thoại", value: selectedUser.phone },
                  { label: "Giới tính", value: selectedUser.gender },
                  { label: "Ngày sinh", value: selectedUser.date_of_birth },
                  { label: "Địa chỉ", value: selectedUser.address, full: true },
                  { label: "CCCD (4 số cuối)", value: selectedUser.cccd_last4 },
                ].map((field) => (
                  <div key={field.label} className={field.full ? "sm:col-span-2" : ""}>
                    <dt className="text-[12px] font-semibold uppercase tracking-[0.05em] text-[var(--text-muted)]">
                      {field.label}
                    </dt>
                    <dd className={`mt-1 text-[14.5px] ${field.value ? "text-[var(--text-primary)] font-medium" : "text-[var(--text-muted)] italic"}`}>
                      {field.value || "Chưa có"}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
            
            <div className="px-6 py-4 border-t border-[var(--border-subtle)] bg-[var(--surface-raised)] flex justify-end">
              <button
                type="button"
                onClick={() => setSelectedUser(null)}
                className="press min-h-10 px-5 rounded-[var(--r-sm)] font-medium text-[14px] bg-[var(--surface-overlay)] border border-[var(--border-strong)] text-[var(--text-primary)] hover:bg-[var(--surface-base)]"
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
