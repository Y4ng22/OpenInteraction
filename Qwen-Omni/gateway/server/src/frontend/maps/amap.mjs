const BASE = 'https://restapi.amap.com'

function key(name, env) {
  const value = String(env[name] || '').trim()
  if (!value) throw new Error(`请先在 Qwen-Omni/.env 填写 ${name}`)
  return value
}

function coordinate(location) {
  const match = /^(-?\d+(?:\.\d{1,6})?),\s*(-?\d+(?:\.\d{1,6})?)$/.exec(String(location || '').trim())
  if (!match) throw new Error('坐标应为“经度,纬度”，最多六位小数')
  const longitude = Number(match[1])
  const latitude = Number(match[2])
  if (longitude < -180 || longitude > 180 || latitude < -90 || latitude > 90) {
    throw new Error('经纬度超出有效范围')
  }
  return `${longitude},${latitude}`
}

async function request(path, params, { fetchImpl = fetch } = {}) {
  const url = new URL(path, BASE)
  for (const [name, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') url.searchParams.set(name, String(value))
  }
  // Never return the URL: it carries a private key in its query string.
  let response
  try {
    response = await fetchImpl(url, { signal: AbortSignal.timeout(8000) })
  } catch {
    throw new Error('高德地图网络请求失败或超时')
  }
  if (!response.ok) throw new Error(`高德地图服务返回 HTTP ${response.status}`)
  const result = await response.json()
  if (result?.status !== '1') {
    if (String(result?.infocode) === '20011') {
      throw new Error('高德未为此 Key 开通海外地图权限；海外坐标的逆地理编码和周边搜索需要向高德申请权限。')
    }
    throw new Error(`高德地图服务拒绝了请求：${String(result?.info || '未知错误')}`)
  }
  return result
}

function isLikelyChina(location) {
  const [longitude, latitude] = coordinate(location).split(',').map(Number)
  return longitude >= 73 && longitude <= 135 && latitude >= 18 && latitude <= 54
}

async function amapCoordinate(location, coordinateSystem, options) {
  const value = coordinate(location)
  if (coordinateSystem !== 'gps' || !isLikelyChina(value)) return value
  const result = await request('/v3/assistant/coordinate/convert', {
    key: key('AMAP_V3_API_KEY', options.env || process.env),
    locations: value,
    coordsys: 'gps',
  }, options)
  return coordinate(String(result.locations || '').split(';')[0])
}

export async function amapGeocode({ address, city = '' }, options = {}) {
  const value = String(address || '').trim()
  if (!value || value.length > 200) throw new Error('请提供不超过 200 字的地址')
  const result = await request('/v3/geocode/geo', {
    key: key('AMAP_V3_API_KEY', options.env || process.env),
    address: value,
    city: String(city || '').trim().slice(0, 60),
  }, options)
  return {
    status: 'ok',
    count: Number(result.count || 0),
    locations: (result.geocodes || []).slice(0, 5).map(item => ({
      address: item.formatted_address,
      location: item.location,
      level: item.level,
      adcode: item.adcode,
    })),
  }
}

export async function amapReverseGeocode({ location, coordinate_system = 'amap' }, options = {}) {
  const result = await request('/v3/geocode/regeo', {
    key: key('AMAP_V3_API_KEY', options.env || process.env),
    location: await amapCoordinate(location, coordinate_system, options),
  }, options)
  const address = typeof result.regeocode?.formatted_address === 'string'
    ? result.regeocode.formatted_address.trim() : ''
  if (!address) {
    throw new Error(isLikelyChina(location)
      ? '高德未返回该坐标的地址。'
      : '高德未返回海外地址；当前 Key 或该地区的数据覆盖不足，不能据此判断你的地址。')
  }
  return {
    status: 'ok',
    address,
    address_component: result.regeocode?.addressComponent || null,
  }
}

export async function amapNearby({ location, coordinate_system = 'amap', keywords = '', radius = 5000, limit = 10 }, options = {}) {
  const search = String(keywords || '').trim()
  if (search.length > 80) throw new Error('搜索关键词不得超过 80 字')
  const distance = Number(radius)
  if (!Number.isInteger(distance) || distance < 0 || distance > 50000) {
    throw new Error('搜索半径应在 0 到 50000 米之间')
  }
  const count = Number(limit)
  if (!Number.isInteger(count) || count < 1 || count > 25) {
    throw new Error('搜索数量应在 1 到 25 之间')
  }
  const result = await request('/v5/place/around', {
    key: key('AMAP_V5_API_KEY', options.env || process.env),
    location: await amapCoordinate(location, coordinate_system, options),
    keywords: search,
    radius: distance,
    page_size: count,
    page_num: 1,
  }, options)
  return {
    status: 'ok',
    count: Number(result.count || 0),
    ...(!isLikelyChina(location) && !Number(result.count || 0)
      ? { coverage_notice: '高德没有返回该海外坐标附近的地点；可能是海外数据或权限覆盖不足。' }
      : {}),
    places: (result.pois || []).slice(0, count).map(item => ({
      name: item.name,
      address: item.address,
      location: item.location,
      distance: item.distance,
      type: item.type,
      id: item.id,
    })),
  }
}

export async function amapWeather({ city }, options = {}) {
  const value = String(city || '').trim()
  if (!value || value.length > 80) throw new Error('请提供中国城市或区县名称')
  const geocoded = await amapGeocode({ address: value, city: value }, options)
  const area = geocoded.locations.find(item => /^\d{6}$/.test(String(item.adcode || '')))
  if (!area) throw new Error('高德没有找到该城市的中国行政区划编码；海外天气请改用联网搜索。')
  const keyValue = key('AMAP_V3_API_KEY', options.env || process.env)
  const [current, forecast] = await Promise.all([
    request('/v3/weather/weatherInfo', { key: keyValue, city: area.adcode, extensions: 'base' }, options),
    request('/v3/weather/weatherInfo', { key: keyValue, city: area.adcode, extensions: 'all' }, options),
  ])
  const live = current.lives?.[0] || null
  const outlook = forecast.forecasts?.[0] || null
  return {
    status: 'ok',
    source: '高德地图天气',
    source_url: 'https://lbs.amap.com/api/webservice/guide/api/weatherinfo',
    city: live?.city || outlook?.city || value,
    adcode: area.adcode,
    current: live && {
      weather: live.weather,
      temperature_c: live.temperature,
      humidity_percent: live.humidity,
      wind_direction: live.winddirection,
      wind_power: live.windpower,
      reported_at: live.reporttime,
    },
    forecast: (outlook?.casts || []).slice(0, 3).map(day => ({
      date: day.date,
      day_weather: day.dayweather,
      night_weather: day.nightweather,
      day_temperature_c: day.daytemp,
      night_temperature_c: day.nighttemp,
    })),
    forecast_reported_at: outlook?.reporttime || null,
  }
}
