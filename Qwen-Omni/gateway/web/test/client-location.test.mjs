import assert from 'node:assert/strict'
import test from 'node:test'
import { performBrowserLocationAction } from '../src/location/client-location.js'
import { GatewayClientProtocolEvent } from '../../shared/protocol/gateway-client-protocol.mjs'

const event = {
  type: GatewayClientProtocolEvent.CLIENT_ACTION_REQUEST,
  name: 'client.tool.get_current_location',
  arguments: {},
}

test('location is requested only through the explicit client tool', async () => {
  assert.equal(await performBrowserLocationAction({ type: 'other' }), null)
  const result = await performBrowserLocationAction(event, {
    geolocation: { getCurrentPosition(success) {
      success({ coords: { longitude: -73.9857, latitude: 40.7484, accuracy: 20 } })
    } },
  })
  assert.equal(result.status, 'completed')
  assert.equal(result.output.location, '-73.985700,40.748400')
  assert.equal(result.output.coordinate_system, 'gps')
})

test('denied browser location never invents coordinates', async () => {
  const result = await performBrowserLocationAction(event, {
    geolocation: { getCurrentPosition(_success, error) { error({ code: 1 }) } },
  })
  assert.equal(result.status, 'failed')
  assert.equal(result.error.code, 'geolocation_permission_denied')
})
