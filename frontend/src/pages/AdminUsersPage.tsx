import { useState, useEffect } from "react";
import {
  adminListUsers,
  adminUpdateUserRole,
  adminUpdateUserStatus,
  type AdminUser,
} from "../lib/agentApi";
import { useToast } from "../lib/toast";
import { Users, Lock, Unlock, Shield } from "lucide-react";

export function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
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
      toast.push("success", "Đã cập nhật quyền thành công");
      fetchUsers();
    } catch (error: any) {
      toast.push("error", error.message || "Cập nhật quyền thất bại");
    }
  };

  const handleStatusChange = async (userId: string, isArchived: boolean) => {
    try {
      await adminUpdateUserStatus(userId, isArchived);
      toast.push("success", isArchived ? "Đã khoá tài khoản" : "Đã mở khoá tài khoản");
      fetchUsers();
    } catch (error: any) {
      toast.push("error", error.message || "Cập nhật trạng thái thất bại");
    }
  };

  if (loading) {
    return (
      <div className="p-8 text-center text-[var(--text-secondary)]">
        Đang tải...
      </div>
    );
  }

  return (
    <div className="p-8 max-w-6xl mx-auto min-h-full">
      <div className="flex items-center gap-4 mb-8">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-500 shadow-inner">
          <Users className="w-6 h-6" strokeWidth={2} />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)]">Quản lý Tài khoản</h1>
          <p className="text-sm text-[var(--text-secondary)] mt-1">Phân quyền và quản lý trạng thái truy cập của người dùng</p>
        </div>
      </div>

      <div className="bg-[var(--surface)]/80 backdrop-blur-md border border-[var(--border-light)] rounded-2xl overflow-hidden shadow-sm hover:shadow-md transition-shadow">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-[var(--surface-hover)] border-b border-[var(--border-light)]">
              <th className="p-4 font-medium text-[var(--text-secondary)]">
                Tài khoản
              </th>
              <th className="p-4 font-medium text-[var(--text-secondary)]">
                Thông tin
              </th>
              <th className="p-4 font-medium text-[var(--text-secondary)]">
                Quyền
              </th>
              <th className="p-4 font-medium text-[var(--text-secondary)]">
                Trạng thái
              </th>
              <th className="p-4 font-medium text-[var(--text-secondary)] text-right">
                Thao tác
              </th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr
                key={user.id}
                className="border-b border-[var(--border-light)] hover:bg-[var(--surface-hover)] transition-colors"
              >
                <td className="p-4">
                  <div className="font-medium">{user.username}</div>
                  <div className="text-sm text-[var(--text-secondary)]">
                    {user.id.slice(0, 8)}...
                  </div>
                </td>
                <td className="p-4">
                  <div className="text-sm">{user.full_name || "—"}</div>
                  <div className="text-sm text-[var(--text-secondary)]">
                    {user.email || user.phone || "—"}
                  </div>
                </td>
                <td className="p-4">
                  {user.role === "provider" ? (
                    <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-purple-500/10 text-purple-500">
                      Provider (Demo)
                    </span>
                  ) : (
                    <select
                      className="bg-[var(--bg-primary)] border border-[var(--border-light)] rounded-lg px-2 py-1 text-sm outline-none focus:border-[var(--primary)]"
                      value={user.role}
                      onChange={(e) =>
                        handleRoleChange(user.id, e.target.value as any)
                      }
                    >
                      <option value="customer">Customer</option>
                      <option value="admin">Admin</option>
                    </select>
                  )}
                </td>
                <td className="p-4">
                  <span
                    className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${
                      user.archived_at
                        ? "bg-red-500/10 text-red-500"
                        : "bg-green-500/10 text-green-500"
                    }`}
                  >
                    {user.archived_at ? "Đã khoá" : "Hoạt động"}
                  </span>
                </td>
                <td className="p-4 text-right">
                  <button
                    onClick={() =>
                      handleStatusChange(user.id, !user.archived_at)
                    }
                    className={`inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      user.archived_at
                        ? "bg-[var(--surface)] border border-[var(--border-light)] text-[var(--text-primary)] hover:bg-[var(--surface-hover)]"
                        : "bg-red-500/10 text-red-500 hover:bg-red-500/20"
                    }`}
                  >
                    {user.archived_at ? (
                      <>
                        <Unlock className="w-4 h-4" /> Mở khoá
                      </>
                    ) : (
                      <>
                        <Lock className="w-4 h-4" /> Khoá
                      </>
                    )}
                  </button>
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="p-8 text-center text-[var(--text-secondary)]"
                >
                  Không có dữ liệu
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
