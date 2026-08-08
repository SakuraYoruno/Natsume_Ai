import assert from 'node:assert/strict'
import test from 'node:test'

import {
  HttpError,
  httpBlob,
  httpGet,
  httpPost,
  httpStreamPost,
  httpStreamRequest,
  httpText,
} from './httpClient.js'

function installFetch(t, implementation) {
  const previous = globalThis.fetch
  globalThis.fetch = implementation
  t.after(() => { globalThis.fetch = previous })
}

test('JSON request normalizes headers and body while preserving explicit options', async (t) => {
  let received = null
  installFetch(t, async (path, options) => {
    received = { path, options }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  const payload = await httpPost('/api/tasks', { task: 'chat' }, {
    headers: { 'X-Trace': 'trace-1' },
  })
  assert.deepEqual(payload, { ok: true })
  assert.equal(received.path, '/api/tasks')
  assert.equal(received.options.method, 'POST')
  assert.equal(received.options.body, JSON.stringify({ task: 'chat' }))
  assert.equal(received.options.headers.get('Content-Type'), 'application/json')
  assert.equal(received.options.headers.get('Accept'), 'application/json, text/plain;q=0.9')
  assert.equal(received.options.headers.get('X-Trace'), 'trace-1')
})

test('GET handles text and 204 without fabricating JSON', async (t) => {
  const responses = [
    new Response('plain', { status: 200, headers: { 'Content-Type': 'text/plain' } }),
    new Response(null, { status: 204 }),
  ]
  installFetch(t, async () => responses.shift())
  assert.equal(await httpGet('/plain'), 'plain')
  assert.equal(await httpGet('/empty'), null)
})

test('HTTP failures expose status, payload and readable detail', async (t) => {
  const responses = [
    new Response(JSON.stringify({ detail: { reason_code: 'STALE' } }), { status: 409, headers: { 'Content-Type': 'application/json' } }),
    new Response('upstream failed', { status: 502, headers: { 'Content-Type': 'text/plain' } }),
    new Response(JSON.stringify({}), { status: 500, headers: { 'Content-Type': 'application/json' } }),
  ]
  installFetch(t, async () => responses.shift())

  await assert.rejects(httpGet('/conflict'), (error) => {
    assert.ok(error instanceof HttpError)
    assert.equal(error.status, 409)
    assert.equal(error.message, JSON.stringify({ reason_code: 'STALE' }))
    assert.equal(error.url, '/conflict')
    return true
  })
  await assert.rejects(httpGet('/upstream'), (error) => error.status === 502 && error.message === 'upstream failed')
  await assert.rejects(httpGet('/fallback'), (error) => error.status === 500 && error.message === 'HTTP 500')
})

test('timeout and caller abort paths remain distinguishable', async (t) => {
  installFetch(t, async (path, options) => {
    assert.ok(options.signal)
    if (path === '/slow') throw new Error('timeout:5ms')
    const error = new Error('caller aborted')
    error.name = 'AbortError'
    throw error
  })
  await assert.rejects(httpGet('/slow', { timeoutMs: 5 }), (error) => {
    assert.ok(error instanceof HttpError)
    assert.equal(error.message, '请求超时：/slow')
    return true
  })
  await assert.rejects(httpGet('/aborted', { signal: new AbortController().signal }), (error) => error instanceof HttpError)
})

test('stream request returns the native response and reports text failures', async (t) => {
  const responses = [
    new Response('stream', { status: 200 }),
    new Response('stream rejected', { status: 503 }),
  ]
  installFetch(t, async () => responses.shift())
  const response = await httpStreamPost('/stream', 'body')
  assert.equal(await response.text(), 'stream')
  await assert.rejects(httpStreamRequest('/stream-fail'), (error) => {
    assert.ok(error instanceof HttpError)
    assert.equal(error.status, 503)
    assert.equal(error.payload, 'stream rejected')
    return true
  })
})

test('stream timeout, text and blob helpers preserve their response type', async (t) => {
  installFetch(t, async (path) => {
    if (path === '/stream-timeout') throw new Error('timeout:2ms')
    if (path === '/text-ok') return new Response('markdown', { status: 200 })
    if (path === '/text-fail') return new Response('', { status: 404 })
    if (path === '/blob-ok') return new Response(new Blob(['voice']), { status: 200 })
    return new Response(new Blob(['bad']), { status: 500 })
  })

  await assert.rejects(httpStreamRequest('/stream-timeout', { timeoutMs: 2 }), (error) => error.message === '请求超时：/stream-timeout')
  assert.equal(await httpText('/text-ok'), 'markdown')
  await assert.rejects(httpText('/text-fail'), (error) => error.status === 404 && error.message === 'HTTP 404')
  assert.equal(await (await httpBlob('/blob-ok')).text(), 'voice')
  await assert.rejects(httpBlob('/blob-fail'), (error) => error.status === 500 && error.payload instanceof Blob)
})
