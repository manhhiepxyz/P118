import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { AppLayout } from './components/AppLayout'
import { ReviewPortalLayout } from './components/ReviewPortalLayout'
import { AdminRoute, AuthProvider, ProtectedRoute, ProviderRoute, useAuth } from './lib/auth'
import { NotificationProvider } from './lib/notifications'
import { AdminDashboardPage } from './pages/AdminDashboardPage'
import { ApartmentLinkPage } from './pages/ApartmentLinkPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { ProfilePage } from './pages/ProfilePage'
import { ProviderReviewPage } from './pages/ProviderReviewPage'
import { RegisterPage } from './pages/RegisterPage'
import { VehicleRegistrationPage } from './pages/VehicleRegistrationPage'
import { WorkflowPage } from './pages/WorkflowPage'
import { WorkflowsPage } from './pages/WorkflowsPage'

/**
 * Trang chủ theo role:
 *  - admin    → dashboard quản trị (giám sát workflow)
 *  - provider → cổng xác thực của bên thứ 3 (không dùng app cư dân)
 *  - customer → HomePage (chat dịch vụ cư dân)
 *
 * Vai trò phân tách ở mức UI + điều hướng; backend vẫn chặn quyền bằng
 * `require_roles` cho từng endpoint.
 */
function HomeRedirect() {
  const { user } = useAuth()
  if (user?.role === 'admin') return <Navigate to="/admin" replace />
  if (user?.role === 'provider') return <Navigate to="/review" replace />
  return <HomePage />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        {/* Thông báo realtime cho bell — nằm TRONG AuthProvider để biết user,
            bao quanh Routes để cả AppLayout lẫn ReviewPortalLayout dùng chung. */}
        <NotificationProvider>
        <Routes>
          {/* Auth — không cần AppLayout (màn hình riêng) */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          {/* Cổng xác thực của bên thứ 3 — trang TOÀN MÀN HÌNH, không sidebar
              P-118, branding riêng (ReviewPortalLayout). ProviderRoute: chỉ
              provider/admin. Không nằm trong AppLayout. */}
          <Route
            path="/review"
            element={
              <ProviderRoute>
                <ReviewPortalLayout />
              </ProviderRoute>
            }
          >
            <Route index element={<ProviderReviewPage />} />
          </Route>

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
                cho dịch vụ còn khoá. Admin/provider bị đưa về đúng dashboard
                của họ qua `HomeRedirect`. */}
            <Route index element={<HomeRedirect />} />
            <Route path="/profile" element={<ProfilePage />} />
            {/* Đường DUY NHẤT khách hàng xác minh căn hộ — gửi hồ sơ kèm ảnh
                giấy tờ, provider duyệt rồi mới mở quyền cư dân. */}
            <Route path="/apartment-link" element={<ApartmentLinkPage />} />
            {/* Path B đăng ký xe SONG SONG với Agent: gửi ảnh → provider duyệt.
                Agent (Path A) tạo xe ngay qua chat; trang này không chặn nó. */}
            <Route path="/vehicle-register" element={<VehicleRegistrationPage />} />
            <Route path="/workflow/:workflowId" element={<WorkflowPage />} />
            <Route path="/workflows" element={<WorkflowsPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />

            {/* Admin — chỉ admin; dashboard chỉ còn giám sát workflow. Việc
                duyệt hồ sơ xác thực đã chuyển hẳn cho cổng bên thứ 3 (`/review`). */}
            <Route
              path="/admin"
              element={
                <AdminRoute>
                  <AdminDashboardPage />
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
        </NotificationProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
