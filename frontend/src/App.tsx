import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { AppLayout } from './components/AppLayout'
import { AdminRoute, AuthProvider, ProtectedRoute } from './lib/auth'
import { AdminDashboardPage } from './pages/AdminDashboardPage'
import { AdminWorkflowDetailPage } from './pages/AdminWorkflowDetailPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { DashboardPage } from './pages/DashboardPage'
import { DetailPage } from './pages/DetailPage'
import { LoginPage } from './pages/LoginPage'
import { ProfilePage } from './pages/ProfilePage'
import { RegisterPage } from './pages/RegisterPage'
import { ReviewPlanPage } from './pages/ReviewPlanPage'
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
            <Route index element={<DashboardPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            <Route path="/review/:workflowId" element={<ReviewPlanPage />} />
            <Route path="/workflow/:workflowId" element={<WorkflowPage />} />
            <Route path="/workflow/:workflowId/detail" element={<DetailPage />} />
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
              path="/admin/workflow/:workflowId"
              element={
                <AdminRoute>
                  <AdminWorkflowDetailPage />
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
