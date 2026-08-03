function operatorSessionToken() {
  return String(process.env.MONITOR_UI_OPERATOR_SESSION_TOKEN || '').trim()
}

function operatorSessionOrigin(baseURL) {
  const url = new URL(baseURL)
  const hostname = url.hostname.toLowerCase()
  const isLoopback = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
  if (url.protocol === 'http:' && !isLoopback) {
    throw new Error('MONITOR_UI_OPERATOR_SESSION_TOKEN refuses non-loopback plaintext HTTP')
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`unsupported Monitor UI session origin protocol: ${url.protocol}`)
  }
  return url.origin
}

async function installOperatorSession(context, baseURL, token) {
  const origin = operatorSessionOrigin(baseURL)
  await context.addCookies([{
    name: 'natsume_operator_session',
    value: token,
    url: origin,
    httpOnly: true,
    sameSite: 'Strict',
    secure: origin.startsWith('https:'),
  }])
}

export async function installOptionalOperatorSession(context, baseURL) {
  const token = operatorSessionToken()
  if (token) {
    await installOperatorSession(context, baseURL, token)
    return true
  }
  if (process.env.MONITOR_UI_SKIP_WEB_SERVER !== '1') {
    await context.route('**/obs/operator-session/bootstrap', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          schema_version: 'Natsume.operator-session.bootstrap.v1',
          status: 'AUTHENTICATED',
          principal_id: 'playwright-fixture-owner',
          query_schema_version: 'Natsume.operator-query.v1',
        }),
      })
    })
  }
  return false
}

export async function installRequiredOperatorSession(context, baseURL) {
  const token = operatorSessionToken()
  if (!token) {
    throw new Error('MONITOR_UI_OPERATOR_SESSION_TOKEN is required for live production acceptance')
  }
  await installOperatorSession(context, baseURL, token)
}
