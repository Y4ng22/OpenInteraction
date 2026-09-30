import { currentTimeSnapshot } from '../../../conversation/frontend-agent-context.mjs'
import { amapGeocode, amapNearby, amapReverseGeocode, amapWeather } from '../../maps/amap.mjs'

export const GET_CURRENT_TIME_TOOL_NAME = 'get_current_time'
export const AMAP_GEOCODE_TOOL_NAME = 'amap_geocode'
export const AMAP_REVERSE_GEOCODE_TOOL_NAME = 'amap_reverse_geocode'
export const AMAP_NEARBY_TOOL_NAME = 'amap_nearby_search'
export const AMAP_WEATHER_TOOL_NAME = 'amap_weather'

const getCurrentTimeTool = {
  type: 'function',
  function: {
    name: GET_CURRENT_TIME_TOOL_NAME,
    description: '获取用户本地时区中的准确当前日期、时间和星期，也可作为相对日期与时间计算的依据。',
    parameters: {
      type: 'object',
      properties: {},
      additionalProperties: false,
    },
  },
}

export const coreToolEntries = [
  { definition: getCurrentTimeTool },
  ...[
    [AMAP_GEOCODE_TOOL_NAME, '将中国地址解析为高德经纬度坐标。', {
      address: { type: 'string', description: '详细地址或地标。' },
      city: { type: 'string', description: '可选城市。' },
    }, ['address']],
    [AMAP_REVERSE_GEOCODE_TOOL_NAME, '将经纬度坐标解析为地址；浏览器定位返回的坐标须标记 coordinate_system=gps。美国等海外地点可能需要高德海外权限。', {
      location: { type: 'string', description: '经度,纬度。' },
      coordinate_system: { type: 'string', enum: ['amap', 'gps'], description: '浏览器定位坐标用 gps；高德返回坐标用 amap。' },
    }, ['location']],
    [AMAP_NEARBY_TOOL_NAME, '按经纬度搜索附近地点；可先用浏览器定位或 amap_geocode 获取中心坐标。海外地点需要高德海外权限。', {
      location: { type: 'string', description: '中心点经度,纬度。' },
      coordinate_system: { type: 'string', enum: ['amap', 'gps'], description: '浏览器定位坐标用 gps；高德返回坐标用 amap。' },
      keywords: { type: 'string', description: '可选地点关键词。' },
      radius: { type: 'integer', minimum: 0, maximum: 50000, description: '半径，米，默认 5000。' },
      limit: { type: 'integer', minimum: 1, maximum: 25, description: '最多返回数量，默认 10。' },
    }, ['location']],
    [AMAP_WEATHER_TOOL_NAME, '快速查询中国城市今天的实时天气和预报，优先用于国内天气问题；海外天气使用联网搜索。', {
      city: { type: 'string', description: '中国城市或区县名称，如上海市。' },
    }, ['city']],
  ].map(([name, description, properties, required]) => ({
    definition: {
      type: 'function',
      function: { name, description, parameters: {
        type: 'object', properties, required, additionalProperties: false,
      } },
    },
  })),
]

export function coreToolHandlers(runtime) {
  const handleAmap = operation => async ({ callId, turnId, args }) => {
    try {
      await runtime.sendOutput(callId, await operation(args), turnId)
    } catch (error) {
      await runtime.sendOutput(callId, {
        status: 'failed', error: true,
        error_code: 'amap_request_failed',
        user_message: error.message || '地图服务暂时不可用。',
      }, turnId)
    }
  }
  return {
    [AMAP_GEOCODE_TOOL_NAME]: handleAmap(amapGeocode),
    [AMAP_REVERSE_GEOCODE_TOOL_NAME]: handleAmap(amapReverseGeocode),
    [AMAP_NEARBY_TOOL_NAME]: handleAmap(amapNearby),
    [AMAP_WEATHER_TOOL_NAME]: handleAmap(amapWeather),
    [GET_CURRENT_TIME_TOOL_NAME]: async ({ callId, turnId }) => {
      await runtime.sendOutput(callId, {
        status: 'ok',
        ...currentTimeSnapshot(runtime.getClientContext()),
      }, turnId)
    },
  }
}
