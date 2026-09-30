import assert from 'node:assert/strict'
import test from 'node:test'
import { amapGeocode, amapNearby, amapReverseGeocode, amapWeather } from '../src/frontend/maps/amap.mjs'

const env = { AMAP_V3_API_KEY: 'v3-secret', AMAP_V5_API_KEY: 'v5-secret' }

test('v3 geocoding uses the v3 key and returns bounded coordinates', async () => {
  let requested
  const result = await amapGeocode({ address: '北京天安门' }, {
    env,
    fetchImpl: async url => {
      requested = url
      return { ok: true, json: async () => ({ status: '1', count: '1', geocodes: [{
        formatted_address: '北京市天安门', location: '116.397499,39.908722', level: '兴趣点',
      }] }) }
    },
  })
  assert.equal(requested.pathname, '/v3/geocode/geo')
  assert.equal(requested.searchParams.get('key'), 'v3-secret')
  assert.equal(result.locations[0].location, '116.397499,39.908722')
})

test('v3 reverse geocoding checks coordinate order', async () => {
  let requested
  const result = await amapReverseGeocode({ location: '116.397499,39.908722' }, {
    env,
    fetchImpl: async url => {
      requested = url
      return { ok: true, json: async () => ({ status: '1', regeocode: { formatted_address: '北京市' } }) }
    },
  })
  assert.equal(requested.pathname, '/v3/geocode/regeo')
  assert.equal(result.address, '北京市')
  await assert.rejects(amapReverseGeocode({ location: '181,39' }, { env }), /超出有效范围/)
})

test('v5 nearby search uses its own key and bounds results', async () => {
  let requested
  const result = await amapNearby({ location: '116.4,39.9', keywords: '咖啡', limit: 1 }, {
    env,
    fetchImpl: async url => {
      requested = url
      return { ok: true, json: async () => ({ status: '1', count: '2', pois: [
        { name: '咖啡 A', location: '116.4,39.9' }, { name: '咖啡 B' },
      ] }) }
    },
  })
  assert.equal(requested.pathname, '/v5/place/around')
  assert.equal(requested.searchParams.get('key'), 'v5-secret')
  assert.equal(requested.searchParams.get('page_size'), '1')
  assert.equal(result.places.length, 1)
})

test('missing keys fail before any network request', async () => {
  await assert.rejects(amapGeocode({ address: '北京' }, { env: {} }), /AMAP_V3_API_KEY/)
  await assert.rejects(amapNearby({ location: '116,39' }, { env: {} }), /AMAP_V5_API_KEY/)
})

test('browser GPS coordinates are converted before domestic nearby search', async () => {
  const paths = []
  const result = await amapNearby({ location: '116.4,39.9', coordinate_system: 'gps' }, {
    env,
    fetchImpl: async url => {
      paths.push(url.pathname)
      return { ok: true, json: async () => url.pathname.includes('/convert')
        ? { status: '1', locations: '116.406,39.906' }
        : { status: '1', count: '0', pois: [] } }
    },
  })
  assert.deepEqual(paths, ['/v3/assistant/coordinate/convert', '/v5/place/around'])
  assert.equal(result.status, 'ok')
})

test('overseas location leaves GPS unchanged and explains missing overseas privileges', async () => {
  let requested
  await assert.rejects(amapNearby({ location: '-73.98,40.75', coordinate_system: 'gps' }, {
    env,
    fetchImpl: async url => {
      requested = url
      return { ok: true, json: async () => ({ status: '0', infocode: '20011', info: 'INSUFFICIENT_ABROAD_PRIVILEGES' }) }
    },
  }), /海外地图权限/)
  assert.equal(requested.pathname, '/v5/place/around')
  assert.equal(requested.searchParams.get('location'), '-73.98,40.75')
})

test('a successful but empty overseas reverse-geocode response is not a location', async () => {
  await assert.rejects(amapReverseGeocode({ location: '-73.98,40.75', coordinate_system: 'gps' }, {
    env,
    fetchImpl: async () => ({ ok: true, json: async () => ({ status: '1', regeocode: { formatted_address: [], addressComponent: { country: [] } } }) }),
  }), /未返回海外地址/)
})

test('domestic weather uses geocoded adcode and returns dated source data', async () => {
  const paths = []
  const result = await amapWeather({ city: '上海市' }, {
    env,
    fetchImpl: async url => {
      paths.push(url.pathname)
      const response = url.pathname.includes('/geocode')
        ? { status: '1', geocodes: [{ adcode: '310000', formatted_address: '上海市' }] }
        : url.searchParams.get('extensions') === 'base'
          ? { status: '1', lives: [{ city: '上海市', weather: '晴', temperature: '24', reporttime: '2026-09-29 20:00:00' }] }
          : { status: '1', forecasts: [{ city: '上海市', reporttime: '2026-09-29 18:00:00', casts: [{ date: '2026-09-29', dayweather: '晴', daytemp: '26', nighttemp: '19' }] }] }
      return { ok: true, json: async () => response }
    },
  })
  assert.deepEqual(paths, ['/v3/geocode/geo', '/v3/weather/weatherInfo', '/v3/weather/weatherInfo'])
  assert.equal(result.current.temperature_c, '24')
  assert.equal(result.forecast[0].date, '2026-09-29')
  assert.equal(result.source, '高德地图天气')
})
