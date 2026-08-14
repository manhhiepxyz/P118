import { useMemo } from 'react'
import {
  Building2,
  Car,
  Compass,
  HelpCircle,
  ParkingSquare,
  ShieldCheck,
  UserCheck,
} from 'lucide-react'

import { SkeletonRows } from '../components/Bits'
import { getWorkflowStatus, listWorkflows } from '../lib/client'
import { formatBuySubType, formatConsultationType, formatTourSlot } from '../lib/status'
import type { WorkflowTask } from '../lib/types'
import { usePolling } from '../lib/usePolling'

interface ResidentAsset {
  resident_id?: string
  full_name?: string
  apartment_code?: string
  residential_area?: string
}

interface VehicleAsset {
  vehicle_id?: string
  plate_number?: string
  vehicle_type?: string
}

interface ParkingAsset {
  booking_id?: string
  parking_zone?: string
  booking_date?: string
  amount?: number
}

interface PaymentRecord {
  payment_id?: string
  amount?: number
  currency?: string
  workflow_id: string
  created_at: string | null
}

interface TourAsset {
  tour_id?: string
  residential_area?: string
  tour_date?: string
  tour_slot?: string
}

interface ShuttleAsset {
  shuttle_id?: string
  tour_id?: string
  passenger_count?: number
}

interface ConsultationAsset {
  consultation_id?: string
  consultation_type?: string
  buy_sub_type?: string
}

/** ProfilePage — Tổng hợp thông tin cư dân, phương tiện, dịch vụ tham quan & thanh toán từ các workflow thành công. */
export function ProfilePage() {
  const { data: summaryList, loading } = usePolling(
    () => listWorkflows().then((r) => r.items),
    15000,
  )

  const successWorkflows = useMemo(() => {
    return (summaryList ?? []).filter((w) => w.status === 'SUCCESS')
  }, [summaryList])

  // Lấy chi tiết các workflow SUCCESS để trích xuất tài sản
  const { data: detailsList, loading: loadingDetails } = usePolling(
    async () => {
      if (successWorkflows.length === 0) return []
      const promises = successWorkflows.slice(0, 10).map((w) =>
        getWorkflowStatus(w.workflow_id).catch(() => null),
      )
      const results = await Promise.all(promises)
      return results.filter(Boolean)
    },
    20000,
    successWorkflows.length > 0,
  )

  const assets = useMemo(() => {
    const residents: ResidentAsset[] = []
    const vehicles: VehicleAsset[] = []
    const parkings: ParkingAsset[] = []
    const payments: PaymentRecord[] = []
    const tours: TourAsset[] = []
    const shuttles: ShuttleAsset[] = []
    const consultations: ConsultationAsset[] = []

    if (!detailsList) return { residents, vehicles, parkings, payments, tours, shuttles, consultations }

    for (const item of detailsList) {
      if (!item) continue
      const { workflow, tasks } = item

      for (const t of tasks as WorkflowTask[]) {
        if (t.status !== 'SUCCESS' || !t.result_data) continue

        if (t.tool === 'register_resident') {
          residents.push({
            resident_id: String(t.result_data.resident_id || ''),
            full_name: String(t.input_data?.full_name || t.result_data.full_name || 'Cư dân'),
            apartment_code: String(t.input_data?.apartment_code || t.result_data.apartment_code || 'A1201'),
            residential_area: String(t.input_data?.residential_area || 'Vinhomes Ocean Park'),
          })
        } else if (t.tool === 'register_vehicle') {
          vehicles.push({
            vehicle_id: String(t.result_data.vehicle_id || ''),
            plate_number: String(t.input_data?.plate_number || t.result_data.plate_number || 'Biển số'),
            vehicle_type: String(t.input_data?.vehicle_type || 'car') === 'car' ? 'Ô tô' : 'Xe máy',
          })
        } else if (t.tool === 'book_parking') {
          parkings.push({
            booking_id: String(t.result_data.booking_id || ''),
            parking_zone: String(t.result_data.parking_zone || t.input_data?.parking_zone || 'ZONE_A'),
            booking_date: String(t.result_data.booking_date || t.input_data?.booking_date || ''),
            amount: Number(t.result_data.amount || 0),
          })
        } else if (t.tool === 'pay_fee') {
          payments.push({
            payment_id: String(t.result_data.payment_id || ''),
            amount: Number(t.result_data.amount || t.input_data?.amount || 0),
            currency: String(t.result_data.currency || 'VND'),
            workflow_id: workflow.workflow_id,
            created_at: workflow.created_at,
          })
        } else if (t.tool === 'book_tour') {
          tours.push({
            tour_id: String(t.result_data.tour_id || ''),
            residential_area: String(t.result_data.residential_area || t.input_data?.residential_area || 'Dự án căn hộ'),
            tour_date: String(t.result_data.tour_date || t.input_data?.tour_date || ''),
            tour_slot: String(t.result_data.tour_slot || t.input_data?.tour_slot || 'MORNING'),
          })
        } else if (t.tool === 'book_shuttle') {
          shuttles.push({
            shuttle_id: String(t.result_data.shuttle_id || ''),
            tour_id: String(t.result_data.tour_id || t.input_data?.tour_id || ''),
            passenger_count: Number(t.result_data.passenger_count || t.input_data?.passenger_count || 1),
          })
        } else if (t.tool === 'register_consultation') {
          consultations.push({
            consultation_id: String(t.result_data.consultation_id || ''),
            consultation_type: String(t.result_data.consultation_type || t.input_data?.consultation_type || 'BUY'),
            buy_sub_type: String(t.result_data.buy_sub_type || t.input_data?.buy_sub_type || ''),
          })
        }
      }
    }

    return { residents, vehicles, parkings, payments, tours, shuttles, consultations }
  }, [detailsList])

  const isLoading = loading || loadingDetails

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-xl font-bold text-gray-900 dark:text-gray-100">
            <UserCheck className="h-6 w-6 text-teal-600" />
            Hồ sơ & Tài sản Cư dân
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Tổng hợp thông tin căn hộ, phương tiện, thẻ xe và lịch sử giao dịch thành công qua AI Agent.
          </p>
        </div>

        <div className="inline-flex items-center gap-2 rounded-xl bg-teal-50 px-3.5 py-2 text-xs font-semibold text-teal-800 dark:bg-teal-950/60 dark:text-teal-300">
          <ShieldCheck className="h-4 w-4" />
          Tài khoản đã xác thực eKYC
        </div>
      </div>

      {isLoading && <SkeletonRows count={3} />}

      {!isLoading && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Căn hộ & Cư dân */}
          <div className="rounded-2xl border border-gray-200 bg-card p-6 shadow-sm dark:border-gray-800">
            <div className="flex items-center gap-2 text-teal-700 dark:text-teal-400">
              <Building2 className="h-5 w-5" />
              <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">Căn hộ đã đăng ký</h2>
            </div>

            {assets.residents.length === 0 ? (
              <p className="mt-4 text-xs text-gray-400">Chưa có thông tin cư dân chính thức.</p>
            ) : (
              <div className="mt-4 space-y-3">
                {assets.residents.map((r, idx) => (
                  <div key={idx} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-900/60">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs text-teal-600 dark:text-teal-400">{r.resident_id}</span>
                      <span className="rounded-md bg-teal-100 px-2 py-0.5 text-[11px] font-bold text-teal-800 dark:bg-teal-900/60 dark:text-teal-200">
                        Căn hộ {r.apartment_code}
                      </span>
                    </div>
                    <p className="mt-2 text-sm font-semibold text-gray-900 dark:text-gray-100">{r.full_name}</p>
                    <p className="mt-0.5 text-xs text-gray-500">{r.residential_area}</p>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Phương tiện */}
          <div className="rounded-2xl border border-gray-200 bg-card p-6 shadow-sm dark:border-gray-800">
            <div className="flex items-center gap-2 text-blue-600 dark:text-blue-400">
              <Car className="h-5 w-5" />
              <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">Phương tiện đăng ký</h2>
            </div>

            {assets.vehicles.length === 0 ? (
              <p className="mt-4 text-xs text-gray-400">Chưa đăng ký phương tiện nào.</p>
            ) : (
              <div className="mt-4 space-y-3">
                {assets.vehicles.map((v, idx) => (
                  <div key={idx} className="flex items-center justify-between rounded-xl bg-gray-50 p-4 dark:bg-gray-900/60">
                    <div>
                      <span className="font-mono text-sm font-bold text-gray-900 dark:text-gray-100">{v.plate_number}</span>
                      <p className="mt-0.5 text-xs text-gray-500">Loại: {v.vehicle_type}</p>
                    </div>
                    <span className="font-mono text-xs text-gray-400">{v.vehicle_id}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Vé đỗ xe */}
          <div className="rounded-2xl border border-gray-200 bg-card p-6 shadow-sm dark:border-gray-800">
            <div className="flex items-center gap-2 text-amber-600 dark:text-amber-400">
              <ParkingSquare className="h-5 w-5" />
              <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">Thẻ / Đặt chỗ đỗ xe</h2>
            </div>

            {assets.parkings.length === 0 ? (
              <p className="mt-4 text-xs text-gray-400">Chưa có lịch đặt chỗ đỗ xe.</p>
            ) : (
              <div className="mt-4 space-y-3">
                {assets.parkings.map((p, idx) => (
                  <div key={idx} className="flex items-center justify-between rounded-xl bg-gray-50 p-4 dark:bg-gray-900/60">
                    <div>
                      <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-bold text-amber-800 dark:bg-amber-950/80 dark:text-amber-300">
                        {p.parking_zone}
                      </span>
                      <p className="mt-2 text-xs text-gray-500">Ngày đỗ: {p.booking_date || 'Hôm nay'}</p>
                    </div>
                    <span className="font-mono text-xs text-gray-400">{p.booking_id}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Lịch tham quan & Xe đưa đón */}
          <div className="rounded-2xl border border-gray-200 bg-card p-6 shadow-sm dark:border-gray-800">
            <div className="flex items-center gap-2 text-purple-600 dark:text-purple-400">
              <Compass className="h-5 w-5" />
              <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">Tham quan Dự án & Xe đưa đón</h2>
            </div>

            {assets.tours.length === 0 ? (
              <p className="mt-4 text-xs text-gray-400">Chưa có lịch đăng ký tham quan dự án.</p>
            ) : (
              <div className="mt-4 space-y-3">
                {assets.tours.map((t, idx) => (
                  <div key={idx} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-900/60">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs text-purple-600 dark:text-purple-400">{t.tour_id}</span>
                      <span className="rounded-md bg-purple-100 px-2 py-0.5 text-[11px] font-bold text-purple-800 dark:bg-purple-950/80 dark:text-purple-300">
                        {formatTourSlot(t.tour_slot)}
                      </span>
                    </div>
                    <p className="mt-2 text-sm font-semibold text-gray-900 dark:text-gray-100">{t.residential_area}</p>
                    <p className="mt-0.5 text-xs text-gray-500">Ngày: {t.tour_date || 'Hôm nay'}</p>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Tư vấn căn hộ */}
          <div className="rounded-2xl border border-gray-200 bg-card p-6 shadow-sm dark:border-gray-800">
            <div className="flex items-center gap-2 text-rose-600 dark:text-rose-400">
              <HelpCircle className="h-5 w-5" />
              <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">Yêu cầu Tư vấn Căn hộ</h2>
            </div>

            {assets.consultations.length === 0 ? (
              <p className="mt-4 text-xs text-gray-400">Chưa có yêu cầu tư vấn căn hộ nào.</p>
            ) : (
              <div className="mt-4 space-y-3">
                {assets.consultations.map((c, idx) => (
                  <div key={idx} className="flex items-center justify-between rounded-xl bg-gray-50 p-4 dark:bg-gray-900/60">
                    <div>
                      <span className="rounded-md bg-rose-100 px-2 py-0.5 text-xs font-bold text-rose-800 dark:bg-rose-950/80 dark:text-rose-300">
                        {formatConsultationType(c.consultation_type)}
                      </span>
                      {c.buy_sub_type && (
                        <p className="mt-2 text-xs font-medium text-gray-700 dark:text-gray-300">
                          Mục đích: {formatBuySubType(c.buy_sub_type)}
                        </p>
                      )}
                    </div>
                    <span className="font-mono text-xs text-gray-400">{c.consultation_id}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
