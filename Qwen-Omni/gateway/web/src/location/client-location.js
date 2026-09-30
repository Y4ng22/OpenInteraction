import { GatewayClientProtocolEvent } from '../../../shared/protocol/gateway-client-protocol.mjs'

export const browserLocationTools = Object.freeze([{
  name: 'get_current_location',
  description: '仅在用户明确询问自己在哪里或当前位置附近的地点时，向浏览器请求一次定位。返回 GPS/WGS84 经纬度；可能需要用户允许定位权限。',
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
}])

export async function performBrowserLocationAction(event, {
  geolocation = globalThis.navigator?.geolocation,
} = {}) {
  if (
    event?.type !== GatewayClientProtocolEvent.CLIENT_ACTION_REQUEST
    || event.name !== 'client.tool.get_current_location'
  ) return null
  if (Object.keys(event.arguments || {}).length) {
    return { status: 'failed', error: { code: 'invalid_arguments', message: '定位工具不接受参数。' } }
  }
  if (!geolocation?.getCurrentPosition) {
    return { status: 'failed', error: { code: 'geolocation_unavailable', message: '当前浏览器或页面无法提供定位。' } }
  }
  return new Promise(resolve => {
    geolocation.getCurrentPosition(position => {
      const latitude = Number(position.coords?.latitude)
      const longitude = Number(position.coords?.longitude)
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
        resolve({ status: 'failed', error: { code: 'geolocation_invalid', message: '浏览器返回了无效坐标。' } })
        return
      }
      resolve({ status: 'completed', output: {
        location: `${longitude.toFixed(6)},${latitude.toFixed(6)}`,
        coordinate_system: 'gps',
        accuracy_m: Math.round(Number(position.coords?.accuracy) || 0),
      } })
    }, error => {
      const denied = error?.code === 1
      resolve({ status: 'failed', error: {
        code: denied ? 'geolocation_permission_denied' : 'geolocation_failed',
        message: denied ? '浏览器定位权限未获允许；请在站点权限中允许定位后重试。' : '浏览器未能获取当前位置；请检查系统定位服务。',
      } })
    }, { enableHighAccuracy: false, timeout: 8000, maximumAge: 30000 })
  })
}
