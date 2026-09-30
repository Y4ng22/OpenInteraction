import assert from 'node:assert/strict'
import test from 'node:test'
import { modelArchitectureOptions } from '../src/model-architectures.js'

const health = {
  ok: true,
  realtimeProviders: [
    { key: 'dashscope', configured: true },
    { key: 'minicpm-o', configured: true },
  ],
}

test('shows three architectures but only a connected route as available', () => {
  const routes = modelArchitectureOptions({ health, connectionState: 'connected' })
  assert.deepEqual(routes.map(route => route.status), [
    'available', 'standby', 'development',
  ])
  assert.deepEqual(routes.map(route => route.selectable), [true, true, false])
})

test('an unconfigured MiniCPM route cannot be selected', () => {
  const routes = modelArchitectureOptions({
    health: { ...health, realtimeProviders: health.realtimeProviders.slice(0, 1) },
    connectionState: 'connected',
  })
  assert.equal(routes[1].status, 'not-configured')
  assert.equal(routes[1].selectable, false)
})

test('switching status belongs to the selected provider, never its neighbor', () => {
  const routes = modelArchitectureOptions({
    health, selectedProvider: 'minicpm-o', connectionState: 'unavailable',
  })
  assert.deepEqual(routes.map(route => route.status), [
    'standby', 'connection-error', 'development',
  ])
})

test('a disconnected gateway marks both remote routes unavailable', () => {
  const routes = modelArchitectureOptions()
  assert.deepEqual(routes.map(route => route.status), [
    'gateway-offline', 'gateway-offline', 'development',
  ])
})
