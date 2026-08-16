import { useMemo } from 'react'
import {
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'

import { JOURNEY_LANES, type JourneyEdge, type JourneyStep } from '../../lib/journeyMock'
import { STEP_STATE } from './stepState'
import { JourneyNode } from './JourneyNode'
import { LaneNode } from './LaneNode'

const nodeTypes = { journey: JourneyNode, lane: LaneNode }

interface Props {
  selectedId: string | null
  onSelect: (id: string | null) => void
  /** Chặng và cạnh — do trang truyền vào, từ dữ liệu thật hoặc dữ liệu mẫu. */
  steps: (JourneyStep & { x: number; y: number; lane: string })[]
  edges: JourneyEdge[]
  /**
   * Vẽ làn ngữ nghĩa hay không.
   *
   * Chỉ bật với dữ liệu mẫu. Dữ liệu thật KHÔNG có làn: thứ backend nói chắc
   * chắn là `depends_on`, còn "THAM QUAN / DI CHUYỂN" là do người viết dữ liệu
   * mẫu tự đặt. Đoán làn theo tên tool là suy diễn nghiệp vụ ở sai tầng.
   */
  lanes?: boolean
}

/**
 * Canvas hành trình — bề mặt CHÍNH khi đã có việc đang chạy.
 *
 * Làn ngữ nghĩa được vẽ như node nền (`zIndex` âm, không chọn được) thay vì
 * bằng phần tử ngoài canvas: làm vậy thì làn pan và zoom cùng với node, không
 * bị lệch khi người dùng kéo canvas.
 *
 * Canvas KHÔNG cho sửa: không kéo node, không nối cạnh, không xoá. Đây là bức
 * tranh hành trình của khách hàng, không phải trình dựng workflow — kế hoạch do
 * agent lập, người dùng thay đổi nó bằng lời ở dock đáy chứ không bằng chuột.
 */
export function JourneyCanvas({ selectedId, onSelect, steps, edges, lanes = false }: Props) {
  const nodes = useMemo<Node[]>(() => {
    const laneNodes: Node[] = (lanes ? JOURNEY_LANES : []).map((lane) => ({
      id: `lane-${lane.id}`,
      type: 'lane',
      position: { x: -40, y: lane.y },
      data: { title: lane.title, height: lane.height },
      draggable: false,
      selectable: false,
      zIndex: -1,
    }))

    const stepNodes: Node[] = steps.map((step) => ({
      id: step.id,
      type: 'journey',
      position: { x: step.x, y: step.y },
      data: { step, selected: step.id === selectedId },
      draggable: false,
      connectable: false,
      zIndex: 1,
    }))

    return [...laneNodes, ...stepNodes]
    // `steps` đổi mỗi nhịp poll — phải nằm trong deps, nếu không canvas đứng
    // im ở snapshot đầu tiên trong khi backend đã chạy xong.
  }, [selectedId, steps, lanes])

  /**
   * Cạnh mang trạng thái của chặng NGUỒN.
   *
   * Đường nối không phải đường trang trí: nó nói dòng công việc đang chảy tới
   * đâu. Cạnh ra từ chặng đang chạy sáng lên và có dòng nét đứt chậm; cạnh ra
   * từ chặng đã xong mờ đi; còn lại giữ mức nền. Nhờ vậy liếc vào canvas là
   * thấy mạch đang ở đâu mà không phải đọc từng node.
   */
  const flowEdges = useMemo<Edge[]>(
    () =>
      edges.map((edge) => {
        const source = steps.find((step) => step.id === edge.source)
        const presence = source ? STEP_STATE[source.state].presence : 'normal'
        const active = source?.state === 'running'
        return {
          ...edge,
          type: 'smoothstep',
          className: active ? 'edge-live' : presence === 'quiet' ? 'edge-done' : '',
          zIndex: 0,
        }
      }),
    [edges, steps],
  )

  return (
    <ReactFlow
      nodes={nodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, node) => {
        if (node.type === 'journey') onSelect(node.id)
      }}
      /* Bấm nền = bỏ chọn → panel phải quay về tóm tắt hành trình. */
      onPaneClick={() => onSelect(null)}
      fitView
      fitViewOptions={{ padding: 0.12 }}
      minZoom={0.4}
      maxZoom={1.5}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      deleteKeyCode={null}
      className="mat-canvas"
    >
      {/* Kết cấu lưới do `.mat-canvas` lo (kẻ ô + chấm giao điểm, theo theme).
          Không chồng thêm `Background` của React Flow: hai lớp lưới lệch pha
          nhau tạo hoa văn moiré khi zoom. */}
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  )
}
