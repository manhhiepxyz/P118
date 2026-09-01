import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { AppLayout } from './components/AppLayout'
import { ReviewPortalLayout } from './components/ReviewPortalLayout'
import { AdminRoute, AuthProvider, ProtectedRoute, ProviderRoute, useAuth } from './lib/auth'
import { NotificationProvider } from './lib/notifications'
import { AdminDashboardPage } from './pages/AdminDashboardPage'
import { AdminUsersPage } from './pages/AdminUsersPage'
import { AdminWorkflowsPage } from './pages/AdminWorkflowsPage'
import { AdminLayout } from './components/AdminLayout'
import { ApartmentLinkPage } from './pages/ApartmentLinkPage'
import { SupportPage } from './pages/SupportPage'
import { VerifyApartmentPage } from './pages/VerifyApartmentPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { LoginPage } from './pages/LoginPage'
import { ProfilePage } from './pages/ProfilePage'
import { ProviderReviewPage } from './pages/ProviderReviewPage'
import { RegisterPage } from './pages/RegisterPage'
import { VehicleRegistrationPage } from './pages/VehicleRegistrationPage'
import { WorkflowPage } from './pages/WorkflowPage'
import { PaymentResultPage } from './pages/PaymentResultPage'
import { ForgotPasswordPage } from './pages/ForgotPasswordPage'
import { JourneyWorkspacePage } from './pages/JourneyWorkspacePage'
import { WorkflowsPage } from './pages/WorkflowsPage'
import { GoogleRegisterPage } from './pages/GoogleRegisterPage'

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
  /**
   * `/` giờ là workspace.
   *
   * Hai bề mặt cùng làm một việc — `HomePage` chat cũ và workspace — buộc người
   * dùng chọn lối vào cho cùng một tác vụ. Dùng `Navigate` chứ không render
   * thẳng, để chỉ có MỘT URL chuẩn cho không gian làm việc; render thẳng sẽ tạo
   * hai địa chỉ cho cùng một màn hình.
   *
   * LƯU Ý: browser E2E vào `/` rồi điền `#goal` (id của `HomePage`). Workspace
   * dùng `#ws-composer`, nên bốn chỗ trong `tests/e2e/browser_acceptance.mjs`
   * sẽ hỏng. Đây là hệ quả có thật của việc đổi route mặc định — cần cập nhật
   * harness, và em KHÔNG tự sửa vì nó nằm ngoài phạm vi frontend.
   */
  return <Navigate to="/workspace" replace />
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
          <Route path="/google-register" element={<GoogleRegisterPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />

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

          {/* Ba trang khách hàng còn lại — cùng vỏ workspace, nằm NGOÀI
              `AppLayout` như ba trang kia. Để trong đó sẽ có hai sidebar. */}
          <Route
            path="/profile"
            element={
              <ProtectedRoute>
                <ProfilePage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/apartment-link"
            element={
              <ProtectedRoute>
                <ApartmentLinkPage />
              </ProtectedRoute>
            }
          />

          {/* Cổng của ĐƠN VỊ XÁC THỰC, phía người nộp hồ sơ.
              Dùng chung vỏ với `/review` — cùng một cổng, hai vai — nên người
              dùng thấy rõ mình đã rời P-118. `/apartment-link` bên Agent chỉ
              còn là cửa vào: nó cho biết đang ở bước nào rồi dẫn sang đây.
              Ranh giới này phản ánh đúng thực tế: xác minh căn hộ KHÔNG nằm
              trong 10 tool của Agent, một đơn vị độc lập mới quyết định. */}
          <Route
            path="/verify"
            element={
              <ProtectedRoute>
                <ReviewPortalLayout audience="applicant" />
              </ProtectedRoute>
            }
          >
            <Route index element={<VerifyApartmentPage />} />
          </Route>

          {/* Hỗ trợ — câu hỏi thường gặp, quyền riêng tư, liên hệ.
              Cùng vỏ workspace như các trang khách hàng khác. */}
          <Route
            path="/support"
            element={
              <ProtectedRoute>
                <SupportPage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/vehicle-register"
            element={
              <ProtectedRoute>
                <VehicleRegistrationPage />
              </ProtectedRoute>
            }
          />

          {/* Chi tiết một hành trình — cùng vỏ workspace.
              Bấm từ Lịch sử vào đây mà rơi sang giao diện cũ là chỗ gãy dễ
              thấy nhất; nên nó phải nằm ngoài `AppLayout` như hai trang kia. */}
          <Route
            path="/workflow/:workflowId"
            element={
              <ProtectedRoute>
                <WorkflowPage />
              </ProtectedRoute>
            }
          />

          {/* Trang user quay về sau cổng thanh toán gateway (VNPay redirect).
              TOÀN MÀN HÌNH, ngoài AppLayout: đây là điểm cuối một luồng tài
              chính, chỉ poll và báo kết quả — không phải nơi điều hướng. */}
          <Route
            path="/payment/result"
            element={
              <ProtectedRoute>
                <PaymentResultPage />
              </ProtectedRoute>
            }
          />

          {/* Lịch sử — cùng vỏ workspace với `/workspace`.
              Nằm NGOÀI `AppLayout` vì nó tự dựng sidebar và khoá chiều cao
              100dvh; để trong đó sẽ có hai sidebar chồng nhau. */}
          <Route
            path="/workflows"
            element={
              <ProtectedRoute>
                <WorkflowsPage />
              </ProtectedRoute>
            }
          />

          {/* Không gian làm việc hành trình — nguyên mẫu desktop.

              Nằm NGOÀI `AppLayout` một cách cố ý: nó khoá chiều cao ở 100dvh và
              tự dựng điều hướng trái của riêng nó. Đặt trong AppLayout thì nó
              thừa hưởng vùng `main` có padding và cuộn dọc — đúng thứ bố cục
              này tồn tại để tránh.

              TODO: dữ liệu còn giả (`lib/journeyMock.ts`). Route bổ sung,
              không thay thế màn hình nào đang chạy. */}
          <Route
            path="/workspace"
            element={
              <ProtectedRoute>
                <JourneyWorkspacePage />
              </ProtectedRoute>
            }
          />

          {/* Admin — chỉ admin; dashboard chỉ còn số liệu vận hành. Cấu trúc lồng nhau qua AdminLayout. */}
          <Route
            path="/admin"
            element={
              <AdminRoute>
                <AdminLayout />
              </AdminRoute>
            }
          >
            <Route index element={<AdminDashboardPage />} />
            <Route path="users" element={<AdminUsersPage />} />
            <Route path="workflows" element={<AdminWorkflowsPage />} />
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
            {/* Đường DUY NHẤT khách hàng xác minh căn hộ — gửi hồ sơ kèm ảnh
                giấy tờ, provider duyệt rồi mới mở quyền cư dân. */}
            {/* Path B đăng ký xe SONG SONG với Agent: gửi ảnh → provider duyệt.
                Agent (Path A) tạo xe ngay qua chat; trang này không chặn nó. */}

            <Route path="/approvals" element={<ApprovalsPage />} />

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
