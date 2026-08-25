/**
 * Duyệt mọi phần việc của ĐƠN VỊ CUNG CẤP cho đúng một workflow.
 *
 * Module này cố ý không có side effect và không đọc biến môi trường. Browser
 * harness lẫn smoke độc lập đều truyền API base + provider token tường minh.
 */
export async function approveProviderWork(
  apiBase,
  workflowId,
  providerToken,
  { maxRounds = 12, quietDelayMs = 2000, fetchImpl = fetch } = {},
) {
  const auth = { Authorization: `Bearer ${providerToken}` }
  const sequence = []
  const done = new Set()
  let quietRounds = 0

  const get = async (path) => {
    const res = await fetchImpl(`${apiBase}${path}`, { headers: auth })
    if (!res.ok) throw new Error(`đọc hàng đợi thất bại: ${path} → HTTP ${res.status}`)
    return res.json()
  }

  const decide = async (path, kind, tool, taskId) => {
    const res = await fetchImpl(`${apiBase}${path}`, {
      method: 'POST',
      headers: { ...auth, 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    })
    if (!res.ok) {
      throw new Error(`duyệt thất bại ${kind}/${tool}/${taskId} → HTTP ${res.status}`)
    }
    sequence.push({ kind, tool, task_id: taskId })
  }

  for (let round = 0; round < maxRounds; round += 1) {
    let worked = false

    const viewing = (await get('/viewing-approvals?status=AWAITING')).items ?? []
    for (const item of viewing) {
      if (String(item.workflow_id) !== String(workflowId)) continue
      const key = `viewing:${item.task_id}`
      if (done.has(key)) {
        throw new Error(`lịch tham quan đã duyệt lại vào hàng đợi: task=${item.task_id}`)
      }
      done.add(key)
      await decide(
        `/viewing-approvals/${workflowId}/decide`,
        'viewing',
        'schedule_property_viewing',
        item.task_id,
      )
      worked = true
    }

    const services = (await get('/service-approvals?status=AWAITING')).items ?? []
    for (const item of services) {
      if (String(item.workflow_id) !== String(workflowId) || item.tool === 'pay_fee') continue
      const key = `service:${item.task_id}`
      if (done.has(key)) {
        throw new Error(`bước đã duyệt lại vào hàng đợi: ${item.tool}/${item.task_id}`)
      }
      done.add(key)
      await decide(
        `/service-approvals/${workflowId}/${item.task_id}/decide`,
        'service',
        item.tool,
        item.task_id,
      )
      worked = true
    }

    if (worked) {
      quietRounds = 0
      continue
    }
    quietRounds += 1
    if (quietRounds >= 3) return sequence
    await new Promise((resolve) => setTimeout(resolve, quietDelayMs))
  }

  throw new Error(
    `hàng đợi không cạn sau ${maxRounds} vòng; đã duyệt: ${sequence.map((item) => item.tool).join(', ')}`,
  )
}
