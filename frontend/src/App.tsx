import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { AppLayout } from './components/AppLayout'
import { AdminRoute, AuthProvider, ProtectedRoute } from './lib/auth'
import { AdminDashboardPage } from './pages/AdminDashboardPage'
import { AdminLinkRequestsPage } from './pages/AdminLinkRequestsPage'
import { AdminResidentLinkPage } from './pages/AdminResidentLinkPage'
import { ApartmentLinkPage } from './pages/ApartmentLinkPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { ProfilePage } from './pages/ProfilePage'
import { RegisterPage } from './pages/RegisterPage'
import { WorkflowPage } from './pages/WorkflowPage'
import { WorkflowsPage } from './pages/WorkflowsPage'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Auth — không cần AppLayout (màn hình riêng) */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          {/* App chính — yêu cầu đăng nhập */}
          <Route
            element={
              <ProtectedRoute>
                <AppLayout />
              </ProtectedRoute>
            }
          >
            {/* Trang chủ là `HomePage`: nó đọc `/capabilities` nên biết dịch
                vụ nào đang mở theo liên kết cư dân đã VERIFIED, và hiện lý do
                cho dịch vụ còn khoá. `DashboardPage` trước đây chiếm route này
                với danh sách gợi ý hardcode, trong đó có "Đăng ký cư dân" —
                việc mà Planner bị chặn ở tầng code, nên bấm vào chỉ dẫn tới
                một vòng hỏi lại vô ích. */}
            <Route index element={<HomePage />} />
            <Route path="/profile" element={<ProfilePage />} />
            {/* Đường DUY NHẤT khách hàng bắt đầu việc liên kết căn hộ. Nó chỉ
                tạo một yêu cầu PENDING — quyền vẫn do admin mở. */}
            <Route path="/apartment-link" element={<ApartmentLinkPage />} />
            <Route path="/workflow/:workflowId" element={<WorkflowPage />} />
            <Route path="/workflows" element={<WorkflowsPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />

            {/* Admin — chỉ admin truy cập */}
            <Route
              path="/admin"
              element={
                <AdminRoute>
                  <AdminDashboardPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/resident-links"
              element={
                <AdminRoute>
                  <AdminResidentLinkPage />
                </AdminRoute>
              }
            />
            {/* Hàng chờ duyệt: admin không còn phải tự gõ UUID tài khoản. */}
            <Route
              path="/admin/link-requests"
              element={
                <AdminRoute>
                  <AdminLinkRequestsPage />
                </AdminRoute>
              }
            />
          </Route>

          {/* Fallback */}
          <Route
            path="*"
            element={
              <div className="py-16 text-center">
                <p className="text-sm text-gray-500">Không tìm thấy trang.</p>
                <a href="/" className="mt-2 inline-block text-sm font-medium text-teal-700 hover:underline">
                  Về trang chủ
                </a>
              </div>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
