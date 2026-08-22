/** Smoke HTTP thật: tham quan → provider duyệt lịch → provider duyệt shuttle. */
import { execFileSync } from 'node:child_process'

import { approveProviderWork } from './provider_approvals.mjs'

const DB = (process.env.P118_DB ?? '').trim()
if (DB !== 'p118_e2e_db') throw new Error('P118_DB phải trỏ chính xác tới database p118_e2e_db.')
const API = process.env.P118_API ?? 'http://127.0.0.1:8080/api/v1'
const PASSWORD = 'MatKhauProviderSmoke!2030'
const stamp = Date.now()
const customerName = `provider_smoke_customer_${stamp}`
const providerName = `provider_smoke_provider_${stamp}`

function sql(database, query) {
  return execFileSync(
    'docker',
    ['exec', 'p118_postgres', 'psql', '-U', 'p118', '-d', database, '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query],
    { encoding: 'utf8', timeout: 60000 },
  ).trim()
}

async function json(path, { token, method = 'GET', body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(`${method} ${path} → HTTP ${response.status}`)
  return payload
}

async function registerAndLogin(username) {
  await json('/auth/register', {
    method: 'POST',
    body: {
      username,
      password: PASSWORD,
      full_name: 'Tài khoản smoke provider',
      phone: '0912-345-678',
    },
  })
  return json('/auth/login', { method: 'POST', body: { username, password: PASSWORD } })
}

async function waitForPause(workflowId, token, answers) {
  let current = workflowId
  for (let round = 0; round < 120; round += 1) {
    const view = await json(`/workflows/demo/${current}`, { token })
    if (view.status === 'NEEDS_INFORMATION') {
      const missing = view.missing_fields ?? []
      const fields = Object.fromEntries(missing.filter((key) => key in answers).map((key) => [key, answers[key]]))
      if (Object.keys(fields).length !== missing.length) {
        throw new Error(`thiếu đáp án smoke cho field: ${missing.filter((key) => !(key in answers)).join(', ')}`)
      }
      const child = await json(`/workflows/demo/${current}/continue`, {
        token,
        method: 'POST',
        body: { fields },
      })
      current = child.workflow_id
      continue
    }
    if (view.status === 'WAITING_APPROVAL') return { workflowId: current, view }
    if (['SUCCESS', 'FAILED', 'CANCELLED', 'PLANNING_ERROR', 'VALIDATION_ERROR', 'EXECUTION_ERROR'].includes(view.status)) {
      return { workflowId: current, view }
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error('workflow không tới điểm dừng trong 120 giây')
}

async function waitForTerminal(workflowId, token) {
  for (let round = 0; round < 120; round += 1) {
    const view = await json(`/workflows/demo/${workflowId}`, { token })
    if (['SUCCESS', 'FAILED', 'CANCELLED', 'PLANNING_ERROR', 'VALIDATION_ERROR', 'EXECUTION_ERROR'].includes(view.status)) {
      return view
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error('workflow không kết thúc trong 120 giây')
}

async function main() {
  const ready = await (await fetch(`${API.replace('/api/v1', '')}/ready`)).json()
  if (ready.status !== 'ready' || !ready.checks?.some((item) => item.name === 'database' && item.detail === 'kết nối được · database=p118_e2e_db')) {
    throw new Error('backend không sẵn sàng trên p118_e2e_db')
  }
  const demoUsersBefore = sql('p118_db', 'SELECT count(*) FROM users')

  const customer = await registerAndLogin(customerName)
  await registerAndLogin(providerName)
  sql(DB, `UPDATE users SET role='provider' WHERE username='${providerName}'`)
  const provider = await json('/auth/login', { method: 'POST', body: { username: providerName, password: PASSWORD } })

  const date = new Date(Date.now() + 70 * 86400000).toISOString().slice(0, 10)
  const started = await json('/workflows/demo/start', {
    token: customer.access_token,
    method: 'POST',
    body: {
      goal: `Đặt lịch tham quan Vinhomes Sài Gòn Park ngày ${date} lúc 09:30 và đặt xe đưa đón cho 2 người`,
      project_name: 'Vinhomes Sài Gòn Park',
    },
  })
  const paused = await waitForPause(started.workflow_id, customer.access_token, {
    project_name: 'Vinhomes Sài Gòn Park',
    viewing_date: date,
    viewing_time: '09:30',
    passenger_count: 2,
  })
  if (paused.view.status !== 'WAITING_APPROVAL') {
    throw new Error(`workflow dừng sai trạng thái: ${paused.view.status}`)
  }

  const sequence = await approveProviderWork(API, paused.workflowId, provider.access_token)
  const final = await waitForTerminal(paused.workflowId, customer.access_token)
  const tools = sequence.map((item) => item.tool)
  if (JSON.stringify(tools) !== JSON.stringify(['schedule_property_viewing', 'book_shuttle'])) {
    throw new Error(`sequence sai: ${tools.join(' → ')}`)
  }
  if (final.status !== 'SUCCESS') throw new Error(`workflow cuối là ${final.status}`)

  const taskEvidence = sql(
    DB,
    `SELECT tool || ':' || status FROM workflow_tasks WHERE workflow_id='${paused.workflowId}'::uuid ORDER BY task_id`,
  ).split('\n').filter(Boolean)
  const awaiting = sql(
    DB,
    `SELECT count(*) FROM service_approvals WHERE workflow_id='${paused.workflowId}'::uuid AND status='AWAITING'`,
  )
  if (awaiting !== '0') throw new Error(`workflow còn ${awaiting} approval AWAITING`)
  const demoUsersAfter = sql('p118_db', 'SELECT count(*) FROM users')
  if (demoUsersAfter !== demoUsersBefore) throw new Error('p118_db đã thay đổi')

  console.log(`sequence=${tools.join(' -> ')}`)
  console.log(`workflow=${paused.workflowId.slice(0, 8)}… status=${final.status}`)
  console.log(`tasks=${taskEvidence.join(', ')}`)
  console.log(`awaiting=${awaiting} p118_db_users=${demoUsersBefore}->${demoUsersAfter}`)
}

main().catch((error) => {
  console.error(`DỪNG: ${error.message}`)
  process.exitCode = 1
})
