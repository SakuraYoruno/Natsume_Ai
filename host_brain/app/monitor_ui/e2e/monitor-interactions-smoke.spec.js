import { expect, test } from '@playwright/test'
import { installOptionalOperatorSession } from './operator-session.js'

function monitorUrl(baseURL, routePath) {
  return new URL(`#${routePath}`, baseURL).toString()
}

async function fulfillJson(route, body, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function readBffPayload(route) {
  const request = route.request()
  const headers = request.headers()
  expect(headers['x-natsume-correlation-id']).toMatch(/^corr\./)
  expect(headers['x-natsume-idempotency-key']).toMatch(/^idem\./)
  expect(headers['x-natsume-nonce']).toMatch(/^nonce\./)
  expect(headers['x-natsume-signature']).toBeUndefined()
  expect(headers.authorization).toBeUndefined()
  const envelope = await request.postDataJSON()
  expect(Object.keys(envelope)).toEqual(['payload'])
  return envelope.payload
}

async function fulfillCommandReceipt(route, commandId, result = {}) {
  await fulfillJson(route, {
    command_id: commandId,
    allowed: true,
    executed: true,
    duplicate: false,
    reason_code: 'COMMAND_COMPLETED',
    status: 'completed',
    audit_ref: `audit://e2e/${commandId}`,
    claim_ref: `claim://e2e/${commandId}`,
    result_ref: `result://e2e/${commandId}`,
    certainty: 'certain',
    ...result,
  })
}

test.beforeEach(async ({ context, baseURL }) => {
  await installOptionalOperatorSession(context, baseURL)
})

async function stubCommonReadApis(page) {
  await page.route('**/obs/runtime-snapshot**', (route) => fulfillJson(route, {
    timestamp: '2026-07-13T15:00:00Z',
    background_runtime: { status: 'healthy' },
  }))
  await page.route('**/obs/prompt-builder/stats**', (route) => fulfillJson(route, {
    total: 0,
    active_templates: 0,
  }))
  await page.route('**/obs/capability-gaps/stats**', (route) => fulfillJson(route, {
    detected_total: 0,
    active_runtime_capabilities: 0,
    hotspots: [],
    forge_candidates: [],
    forge_runtime_quality: null,
  }))
}

async function stubSecurityImmuneApis(page) {
  await stubCommonReadApis(page)
  const state = { huntCalls: [] }
  const riskOrder = { low: 1, medium: 2, high: 3, critical: 4 }
  const allIncidents = [
    {
      incident_id: 'incident-high-powershell',
      title: 'Suspicious PowerShell execution',
      risk: 'high',
      detail: 'Encoded PowerShell launched from endpoint-1.',
      asset_id: 'endpoint-1',
      source: 'edr.rule.powershell.encoded',
      evidence_refs: ['evidence://security/powershell-encoded'],
      audit_ref: 'ledger://security/incident-high-powershell',
      recommendation: 'Isolate endpoint-1 and confirm the encoded command lineage.',
    },
    {
      incident_id: 'incident-low-smb',
      title: 'Legacy SMB service exposed',
      risk: 'low',
      detail: 'SMBv1-compatible service observed on lab subnet.',
      asset_id: 'edge-node-1',
      source: 'network.scan.smb',
    },
  ]
  await page.route('**/obs/security/runtime', (route) => fulfillJson(route, {
    asset_count: 2,
    incident_count: 2,
    risk_counts: { low: 1, high: 1 },
    generated_ts_ms: 1_783_955_600_000,
  }))
  await page.route('**/obs/security/assets', (route) => fulfillJson(route, {
    asset_count: 2,
    assets: [
      { asset_id: 'endpoint-1', hostname: 'endpoint-1', os_family: 'Windows' },
      { asset_id: 'edge-node-1', hostname: 'edge-node-1', os_family: 'Linux' },
    ],
    generated_ts_ms: 1_783_955_600_000,
  }))
  await page.route('**/obs/security/incidents**', (route) => {
    const minRisk = new URL(route.request().url()).searchParams.get('min_risk') || 'low'
    const threshold = riskOrder[minRisk] || riskOrder.low
    const incidents = allIncidents.filter((incident) => riskOrder[incident.risk] >= threshold)
    return fulfillJson(route, {
      incident_count: incidents.length,
      incidents,
      risk_counts: { low: 1, high: 1 },
      generated_ts_ms: 1_783_955_600_000,
    })
  })
  await page.route('**/v1/host/operator-command-bff/security.hunt.run_once', async (route) => {
    const payload = await readBffPayload(route)
    state.huntCalls.push(payload)
    await fulfillCommandReceipt(route, 'security.hunt.run_once', {
      reason: payload.reason,
      asset_count: 2,
      incident_count: 2,
    })
  })
  return state
}

async function stubToolsApis(page) {
  await stubCommonReadApis(page)
  const state = { executeCalls: [] }
  const tools = [
    {
      id: 'read.echo',
      name: 'Read Echo',
      desc: 'Readonly smoke tool',
      side_effect_level: 'READ',
      auth_level: 'NONE',
      version: '1.0.0',
      schema_hash: 'abcdef0123456789abcdef',
      domain_tags: ['smoke', 'read'],
    },
    {
      id: 'write.note',
      name: 'Write Note',
      desc: 'Write smoke tool',
      side_effect_level: 'WRITE',
      auth_level: 'APPROVAL_REQUIRED',
      version: '1.0.0',
      schema_hash: '1234567890abcdef123456',
      domain_tags: ['smoke', 'write'],
    },
  ]
  await page.route('**/tools/health**', (route) => fulfillJson(route, {
    status: 'OK',
    catalog_size: 2,
    read_tools: 1,
    write_tools: 1,
    updated_at: '2026-07-13T15:00:00Z',
  }))
  await page.route('**/tools/meta-pack**', (route) => {
    const query = new URL(route.request().url()).searchParams.get('query')?.toLowerCase() || ''
    const filtered = query
      ? tools.filter((tool) => `${tool.id} ${tool.name} ${tool.desc}`.toLowerCase().includes(query))
      : tools
    return fulfillJson(route, { tool_meta_pack: filtered })
  })
  await page.route('**/v1/host/operator-command-bff/tool.execute', async (route) => {
    const payload = await readBffPayload(route)
    state.executeCalls.push(payload)
    await fulfillCommandReceipt(route, 'tool.execute', {
      tool_id: payload.tool_id,
      intent_id: payload.intent_id,
      session_id: payload.session_id,
    })
  })
  return state
}

async function stubEdgeApis(page) {
  await stubCommonReadApis(page)
  const state = { bffCalls: 0, legacyCalls: 0 }
  await page.route('**/internal/edge_command/status', (route) => fulfillJson(route, {
    enabled: true,
    edge_base_url: 'http://127.0.0.1:19090',
    queue_depth: 0,
  }))
  await page.route('**/v1/host/operator-command-bff/edge.command.push', async (route) => {
    state.bffCalls += 1
    await fulfillJson(route, { detail: 'unexpected request' }, 500)
  })
  await page.route('**/internal/edge_command/push', async (route) => {
    state.legacyCalls += 1
    await fulfillJson(route, { detail: 'legacy writer retired' }, 410)
  })
  return state
}

async function stubGatewayApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/obs/gateway/stats**', (route) => fulfillJson(route, {
    total_inbound: 12,
    total_outbound: 8,
    total_errors: 2,
    humanize_enabled: true,
    humanize_typing_speed_cps: 8,
    platforms: [
      {
        platform: 'wechat',
        status: 'UP',
        inbound_count: 8,
        outbound_count: 5,
        error_count: 0,
        avg_latency_ms: 42,
        bridge_status: 'connected',
        last_event_ts_ms: 1_783_955_600_000,
      },
      {
        platform: 'telegram',
        status: 'DEGRADED',
        inbound_count: 3,
        outbound_count: 2,
        error_count: 2,
        avg_latency_ms: 180,
        status_detail: 'Webhook retrying',
        bridge_status: 'retrying',
        last_event_ts_ms: 1_783_955_601_000,
      },
      {
        platform: 'qq',
        status: 'DOWN',
        inbound_count: 1,
        outbound_count: 1,
        error_count: 0,
        status_detail: 'adapter offline',
      },
    ],
  }))
  await page.route('**/obs/wechat/stats**', (route) => fulfillJson(route, {
    rate_limits: [{ key: 'send_text', current: 10, limit: 10, remaining: 0 }],
    recent_events: [{ ts_ms: 1_783_955_600_000, event_type: 'wechat.message.received' }],
  }))
}

function reviewFixture(reviewId, status, { expired = false } = {}) {
  return {
    review_id: reviewId,
    review_type: 'PRE_EXECUTION_APPROVAL',
    subject_ref: `task-node://graph-smoke/${reviewId}`,
    subject_version: 4,
    requester: 'runtime',
    reason: `Governed review ${reviewId}`,
    risk_level: 4,
    status,
    persisted_status: status,
    status_version: 1,
    expires_at: expired ? 1 : 4_102_444_800_000,
    expired,
    resume_phase: 'PRE_EXECUTION',
    checkpoint_id: `checkpoint://graph-smoke/${reviewId}/4`,
    evidence_refs: [`evidence://graph-smoke/${reviewId}`],
    actions: {
      approve: status === 'PENDING' && !expired,
      reject: status === 'PENDING' && !expired,
      resume: status === 'APPROVED' && !expired,
      resume_requires_token: true,
      resume_binding_fields: [
        'resume_token_ref',
        'subject_ref',
        'subject_version',
        'resume_phase',
        'checkpoint_id',
      ],
    },
  }
}

async function stubTaskRuntimeApis(page) {
  await stubCommonReadApis(page)
  const state = {
    statuses: {
      'review-pending': 'PENDING',
      'review-approved': 'APPROVED',
      'review-expired': 'EXPIRED',
    },
    bffRequests: [],
    resumeCalls: [],
  }
  await page.route('**/obs/task-runtime/executions**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/obs/task-runtime/executions') {
      return fulfillJson(route, {
        schema_version: 'Natsume.operator.execution_index.v1',
        generated_at_ms: 1_783_955_600_000,
        read_only: true,
        executions: [{
          graph_id: 'graph-smoke',
          revision: 7,
          root_work_order_id: 'work-smoke',
          status: 'COMPLETED',
          terminal: true,
          node_count: 1,
          updated_ts_ms: 1_783_955_600_000,
        }],
      })
    }
    const reviews = Object.entries(state.statuses).map(([reviewId, status]) => (
      reviewFixture(reviewId, status, { expired: reviewId === 'review-expired' })
    ))
    return fulfillJson(route, {
      schema_version: 'Natsume.operator.execution_review.v1',
      generated_at_ms: 1_783_955_600_000,
      read_only: true,
      mutation_contract: {
        direct_database_write: false,
        review_commands: ['approve', 'reject', 'resume'],
        resume_requires_bound_token: true,
        automatic_resume: false,
      },
      execution: {
        graph_id: 'graph-smoke',
        revision: 7,
        root_work_order_id: 'work-smoke',
        planner_ref: 'planner://smoke/7',
        status: 'COMPLETED',
        terminal: true,
      },
      dag: {
        nodes: [{
          node_id: 'execute',
          dependencies: [],
          route: 'worker.execute',
          risk_level: 4,
          status: 'ACCEPTED',
          attempts: [{ attempt_id: 'attempt-execute', attempt_no: 1, status: 'SUCCEEDED' }],
          verification: { status: 'PASS', evidence_refs: ['evidence://execute/verify'] },
        }],
        edges: [],
      },
      attempts: [{ attempt_id: 'attempt-execute', node_id: 'execute', attempt_no: 1, status: 'SUCCEEDED' }],
      budgets: [{
        budget_id: 'budget-smoke',
        scope: 'graph-smoke',
        node_id: 'execute',
        status: 'CONSUMED',
        consumed: { token_count: 420, tool_call_count: 3, time_ms: 900 },
        limits: { token_count: 1000, tool_call_count: 10, time_ms: 5000 },
        exceeded_dimensions: [],
      }],
      verification: [{
        node_id: 'execute',
        status: 'PASS',
        source: 'runtime_event',
        evidence_refs: ['evidence://execute/verify'],
      }],
      reviews,
      timeline: [
        {
          cursor: 1,
          event_id: 'event-created',
          event_type: 'TASK_GRAPH_CREATED',
          summary: 'Plan created',
          payload_ref: 'taskstore://event/1',
          evidence_refs: [],
          dedupe_key: 'd1',
          ts_ms: 1_783_955_600_001,
        },
        {
          cursor: 7,
          event_id: 'event-complete',
          event_type: 'TASK_GRAPH_COMPLETED',
          summary: 'Execution terminal',
          payload_ref: 'taskstore://event/7',
          evidence_refs: ['evidence://complete'],
          dedupe_key: 'd7',
          ts_ms: 1_783_955_600_007,
        },
      ],
      cursor_page: { after: 0, next_cursor: 7, event_count: 2, has_more: false, dedupe_field: 'dedupe_key' },
    })
  })
  page.on('request', (request) => {
    if (request.url().includes('/v1/host/operator-command-bff/')) {
      state.bffRequests.push(request.url())
    }
  })
  await page.route('**/v1/host/operator-command-bff/task_review.resume', async (route) => {
    const payload = await readBffPayload(route)
    state.resumeCalls.push(payload)
    state.statuses[payload.review_id] = 'CONSUMED'
    await fulfillCommandReceipt(route, 'task_review.resume', {
      terminal_state: 'CONSUMED',
    })
  })
  return state
}

test.describe('Canonical monitor interactions', () => {
  test('desktop and mobile navigation only expose canonical Security routes', async ({ page, baseURL }) => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await stubSecurityImmuneApis(page)
    await page.goto(monitorUrl(baseURL, '/security/immune'), { waitUntil: 'domcontentloaded' })

    const mainNav = page.getByRole('navigation', { name: '主导航' })
    await expect(mainNav.getByRole('link', { name: /安全免疫/ })).toBeVisible()
    await expect(mainNav.getByRole('link', { name: /渗透实验室/ })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '安全免疫', exact: true })).toBeVisible()

    await page.setViewportSize({ width: 390, height: 1000 })
    const routeButton = page.getByRole('button', { name: /打开导航菜单：Security Lab \/ 安全免疫/ })
    await expect(routeButton).toBeVisible()
    await routeButton.click()
    const mobileNav = page.getByRole('navigation', { name: '移动端导航' })
    await expect(mobileNav.getByRole('link', { name: /安全免疫/ })).toBeVisible()
    await expect(mobileNav.getByText('渗透实验室')).toHaveCount(0)
  })

  test('Security Immune reads projections and submits hunt through generated BFF', async ({ page, baseURL }) => {
    const state = await stubSecurityImmuneApis(page)
    await page.goto(monitorUrl(baseURL, '/security/immune'), { waitUntil: 'domcontentloaded' })

    const incidents = page.locator('#security-immune-incidents')
    await expect(incidents.getByText('Suspicious PowerShell execution', { exact: true })).toBeVisible()
    await expect(incidents.getByText('Legacy SMB service exposed', { exact: true })).toBeVisible()
    await page.getByLabel('Security Immune minimum risk').selectOption('high')
    await expect(incidents.getByText('Legacy SMB service exposed', { exact: true })).toBeHidden()

    await page.getByRole('region', { name: 'Security Immune Gate' })
      .getByRole('button', { name: 'Run Hunt' })
      .click()
    await expect(page.getByText('Hunt 已完成')).toBeVisible()
    await expect.poll(() => state.huntCalls).toEqual([{ reason: 'manual_web_security_immune' }])
    await expect(page.locator('pre').filter({ hasText: 'security.hunt.run_once' })).toBeVisible()
  })

  test('Tools preserves approval gate and accepts only a durable BFF receipt', async ({ page, baseURL }) => {
    const state = await stubToolsApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/tools'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '工具管理' })).toBeVisible()
    const search = page.getByPlaceholder('搜索工具名或描述...')
    await search.fill('write')
    await expect(page.getByText('Write Note')).toBeVisible()
    await expect(page.getByText('Read Echo')).toBeHidden()
    await page.getByRole('button', { name: /Write Note/ }).click()
    await page.getByRole('button', { name: '执行该工具' }).click()

    const submit = page.getByRole('button', { name: '执行', exact: true })
    await expect(submit).toBeDisabled()
    await page.getByPlaceholder('approval_token_ref').fill('approval://smoke')
    await expect(submit).toBeEnabled()
    await submit.click()

    await expect(page.getByText('执行完成')).toBeVisible()
    await expect.poll(() => state.executeCalls.length).toBe(1)
    expect(state.executeCalls[0]).toMatchObject({
      tool_id: 'write.note',
      approval_token_ref: 'approval://smoke',
    })
    await expect(page.locator('pre').filter({ hasText: '"command_id": "tool.execute"' })).toBeVisible()
  })

  test('Edge form validates locally and fails closed before any retired writer', async ({ page, baseURL }) => {
    const state = await stubEdgeApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/edge-command'), { waitUntil: 'domcontentloaded' })

    const send = page.getByRole('button', { name: '发送到 Edge' })
    await expect(send).toBeDisabled()
    await page.getByRole('checkbox', { name: '开启控制门' }).check()
    const payload = page.getByLabel('payload JSON')
    await payload.fill('{bad json')
    await expect(send).toBeDisabled()
    await expect(payload).toHaveAttribute('aria-invalid', 'true')
    await payload.fill('{"emotion_tag":"CALM","intensity":0.2}')
    await expect(send).toBeEnabled()
    await send.click()

    await expect(page.getByText('Push 失败', { exact: true })).toBeVisible()
    await expect(page.getByText('OPERATOR_COMMAND_PAYLOAD_FIELD_DENIED')).toBeVisible()
    expect(state.bffCalls).toBe(0)
    expect(state.legacyCalls).toBe(0)
  })

  test('Gateway remains a projection-backed read surface with no retired reset mutation', async ({ page, baseURL }) => {
    await stubGatewayApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/gateway'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '统一消息网关' })).toBeVisible()
    const gate = page.getByRole('region', { name: 'Gateway Routing Gate' })
    await expect(gate.getByText('3/3 active，1 down，1 degraded。')).toBeVisible()
    await expect(page.locator('#gateway-wechat-detail').getByText('send_text')).toBeVisible()
    await expect(page.getByRole('button', { name: '重置频率限制' })).toHaveCount(0)
    await expect(page.getByText('adapter offline')).toBeVisible()
  })

  test('Task Review is read-only, rejects actor spoofing, and resumes through BFF', async ({ page, baseURL }) => {
    const state = await stubTaskRuntimeApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/task-runtime'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: 'graph-smoke@7 · COMPLETED' })).toBeVisible()
    const reviews = page.getByRole('region', { name: 'Review command rail' })
    await expect(reviews.getByText('review-expired', { exact: true })).toBeVisible()
    await expect(reviews.getByRole('button', { name: '批准 review-expired' })).toHaveCount(0)

    await reviews.getByRole('button', { name: '批准 review-pending' }).click()
    await expect(page.getByText('OPERATOR_COMMAND_PAYLOAD_FIELD_DENIED')).toBeVisible()
    expect(state.bffRequests).toHaveLength(0)

    const token = reviews.getByLabel('Resume token review-approved')
    const resume = reviews.getByRole('button', { name: '恢复 review-approved' })
    await expect(resume).toBeDisabled()
    await token.fill('review-resume.v1.explicit-token')
    await resume.click()
    await expect.poll(() => state.resumeCalls.length).toBe(1)
    expect(state.resumeCalls[0]).toEqual({
      review_id: 'review-approved',
      resume_token_ref: 'review-resume.v1.explicit-token',
      subject_ref: 'task-node://graph-smoke/review-approved',
      subject_version: 4,
      resume_phase: 'PRE_EXECUTION',
      checkpoint_id: 'checkpoint://graph-smoke/review-approved/4',
    })
    await expect(page.getByText(/review-approved 命令已受理/)).toBeVisible()
    expect(state.bffRequests).toHaveLength(1)
  })
})
