import { HttpError, httpPost } from '../../shared/api/httpClient.js'

export const OPERATOR_SESSION_BOOTSTRAP_PATH = '/obs/operator-session/bootstrap'
export const OPERATOR_SESSION_BOOTSTRAP_SCHEMA = 'Natsume.operator-session.bootstrap.v1'

export async function bootstrapLocalOperatorSession() {
  const payload = await httpPost(OPERATOR_SESSION_BOOTSTRAP_PATH, {})
  if (
    !payload
    || payload.schema_version !== OPERATOR_SESSION_BOOTSTRAP_SCHEMA
    || payload.status !== 'AUTHENTICATED'
    || !String(payload.principal_id || '').trim()
  ) {
    throw new HttpError('本机控制台返回了无效的会话结果', {
      status: 502,
      payload,
      url: OPERATOR_SESSION_BOOTSTRAP_PATH,
    })
  }
  return payload
}

export function operatorSessionErrorMessage(error) {
  const status = Number(error?.status || 0)
  if (status === 401 || status === 403) {
    return '当前页面不是从本机 Monitor 入口打开，无法建立所有者会话。'
  }
  if (status === 503) {
    return '本机 Monitor 会话尚未就绪，请重启 Host 服务栈后重试。'
  }
  if (status === 404 || status === 405) {
    return '当前 Host 尚未提供本机 Monitor 会话入口，请更新并重启 Host 服务栈。'
  }
  return '暂时无法连接本机 Monitor 服务，请确认 Host、OBS 与 Nginx Monitor 正在运行。'
}
