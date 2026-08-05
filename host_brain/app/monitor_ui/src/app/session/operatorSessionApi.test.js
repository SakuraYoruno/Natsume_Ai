import assert from 'node:assert/strict'
import test from 'node:test'

import {
  bootstrapLocalOperatorSession,
  operatorSessionErrorMessage,
} from './operatorSessionApi.js'
import { HttpError } from '../../shared/api/httpClient.js'


test('本机会话只接受明确的已认证响应', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({
    schema_version: 'Natsume.operator-session.bootstrap.v1',
    status: 'AUTHENTICATED',
    principal_id: 'local-monitor-owner',
  }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
  try {
    const payload = await bootstrapLocalOperatorSession()
    assert.equal(payload.principal_id, 'local-monitor-owner')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('缺失或拒绝本机会话入口时保持显式失败', () => {
  assert.match(operatorSessionErrorMessage(new HttpError('missing', { status: 404 })), /更新并重启 Host/)
  assert.match(operatorSessionErrorMessage(new HttpError('denied', { status: 403 })), /不是从本机 Monitor/)
  assert.match(operatorSessionErrorMessage(new HttpError('down', { status: 503 })), /重启 Host/)
})
