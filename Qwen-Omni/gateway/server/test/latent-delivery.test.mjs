import assert from 'node:assert/strict'
import { once } from 'node:events'
import test from 'node:test'
import { WebSocketServer } from 'ws'
import { createAgentDelivery } from '../src/delivery/agent-delivery.mjs'
import { TaskManager } from '../src/task/task-manager.mjs'
import { AnnouncementManager } from '../src/voice/announcement/announcement-manager.mjs'
import { RealtimeAgentDeliveryRuntime } from '../src/voice/realtime-agent-delivery-runtime.mjs'
import { RealtimeFrontend } from '../src/voice/realtime-provider.mjs'
import { gptLiveProvider } from '../src/voice/providers/gpt-live.mjs'
import { validateRealtimeProvider } from '../src/voice/providers/registry.mjs'

const REF = Object.freeze({
  version: 1,
  taskId: 'task_1',
  seq: 1,
  key: 'task_1/1',
  final: true,
  model: 'stub',
  layer: -1,
  tokens: 16,
  dim: 64,
  dtype: 'float32',
  createdAtMs: 1,
})

async function waitFor(condition, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error('condition not reached')
    await new Promise(resolve => setTimeout(resolve, 5))
  }
}

// Minimal OpenAI GA Realtime peer: acknowledges items and completes responses.
async function realtimePeer(t) {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await once(server, 'listening')
  const messages = []
  let replies = 0
  server.on('connection', socket => {
    const send = event => socket.send(JSON.stringify(event))
    socket.on('message', raw => {
      const message = JSON.parse(raw.toString())
      messages.push(message)
      if (message.type === 'session.update') send({ type: 'session.updated', session: {} })
      else if (message.type === 'conversation.item.create') {
        send({ type: 'conversation.item.created', item: message.item })
      } else if (message.type === 'response.create') {
        const response = { id: `response_${++replies}`, metadata: message.response?.metadata }
        send({ type: 'response.created', response })
        send({ type: 'response.done', response: { ...response, status: 'completed' } })
      }
    })
    send({ type: 'session.created', session: {} })
  })
  t.after(async () => {
    for (const socket of server.clients) socket.terminate()
    await new Promise(resolve => server.close(resolve))
  })
  return { messages, url: `ws://127.0.0.1:${server.address().port}` }
}

async function connectFrontend(t, provider) {
  const frontend = new RealtimeFrontend({
    provider,
    onEvent: () => {},
    onError: error => { throw error },
    responseStartTimeoutMs: 2000,
    responseCancelGraceMs: 1000,
  })
  t.after(() => frontend.close())
  await frontend.connect()
  return frontend
}

function testProvider(url, overrides = {}) {
  return {
    ...gptLiveProvider,
    key: 'latent-test',
    isConfigured: () => true,
    url: () => url,
    headers: () => ({}),
    ...overrides,
  }
}

test('AgentDelivery carries valid latentRefs and keeps its old shape without them', () => {
  const plain = createAgentDelivery({ mode: 'respond', text: 'result' })
  assert.equal('latentRefs' in plain, false)
  const withRefs = createAgentDelivery({
    mode: 'respond',
    text: 'result',
    latentRefs: [REF, null, 'task_1/1'],
  })
  assert.equal(withRefs.latentRefs.length, 1)
  assert.equal(Object.isFrozen(withRefs.latentRefs), true)
})

test('the delivery runtime forwards latentRefs only when the delivery has them', async () => {
  const calls = []
  const runtime = new RealtimeAgentDeliveryRuntime({
    getFrontend: () => ({
      ready: true,
      injectDelivery: async (...args) => {
        calls.push(args[3])
        return { completed: true }
      },
    }),
  })
  await runtime.deliver({ mode: 'respond', text: 'a' })
  await runtime.deliver({ mode: 'respond', text: 'b', latentRefs: [REF] })
  assert.equal('latentRefs' in calls[0], false)
  assert.equal(calls[1].latentRefs[0].key, 'task_1/1')
})

test('a provider that declares latentInjection must implement buildLatentInjection', () => {
  assert.throws(
    () => validateRealtimeProvider(testProvider('ws://127.0.0.1:1', {
      capabilities: { ...gptLiveProvider.capabilities, latentInjection: true },
    })),
    /latentInjection.*buildLatentInjection/,
  )
})

test('frontends without latentInjection keep the text result injection', async t => {
  const peer = await realtimePeer(t)
  const frontend = await connectFrontend(t, testProvider(peer.url))
  assert.equal('latentInjection' in frontend.capabilities, false)
  const outcome = await frontend.injectDelivery('task text', 'announcement', {}, { latentRefs: [REF] })
  assert.equal(outcome.completed, true)
  assert.equal('latentInjected' in outcome, false)
  const item = peer.messages.find(message => message.type === 'conversation.item.create')
  assert.match(JSON.stringify(item), /task text/)
  assert.doesNotMatch(JSON.stringify(peer.messages), /latent/)
})

test('frontends with latentInjection receive the refs through buildLatentInjection', async t => {
  const peer = await realtimePeer(t)
  const built = []
  const provider = testProvider(peer.url, {
    capabilities: { ...gptLiveProvider.capabilities, latentInjection: true },
    buildLatentInjection: (content, latentRefs, options) => {
      built.push({ content, latentRefs, options })
      const injection = gptLiveProvider.buildResultInjection(content, options)
      return { ...injection, item: { ...injection.item, latent_refs: latentRefs } }
    },
  })
  const frontend = await connectFrontend(t, provider)

  const textOnly = await frontend.injectDelivery('no refs', 'announcement')
  assert.equal(textOnly.completed, true)
  assert.equal(built.length, 0)

  const outcome = await frontend.injectDelivery('task text', 'announcement', {}, { latentRefs: [REF] })
  assert.equal(outcome.completed, true)
  assert.equal(outcome.latentInjected, true)
  assert.equal(built[0].content, 'task text')
  assert.equal(built[0].latentRefs[0].key, 'task_1/1')
  const item = peer.messages.filter(message => message.type === 'conversation.item.create').at(-1)
  assert.equal(item.item.latent_refs[0].key, 'task_1/1')
  assert.match(JSON.stringify(item), /task text/)
})

test('a completed task keeps its latentRef through the snapshot into the announcement', async () => {
  const manager = new TaskManager()
  const created = manager.create({
    objective: 'weather',
    ownerId: 'owner',
    runner: async () => ({ content: 'sunny', latentRef: { ...REF, taskId: 'task_1', key: 'task_1/1' } }),
  })
  await manager.wait(created.id)
  const snapshot = manager.get(created.id, { ownerId: 'owner' })
  assert.equal(snapshot.result, 'sunny')
  assert.equal(snapshot.latentRef.key, 'task_1/1')

  const plain = manager.create({ objective: 'plain', ownerId: 'owner', runner: async () => ({ content: 'ok' }) })
  await manager.wait(plain.id)
  assert.equal('latentRef' in manager.get(plain.id, { ownerId: 'owner' }), false)

  const calls = []
  const announcements = new AnnouncementManager({
    getFrontend: () => ({
      ready: true,
      injectDelivery: async (...args) => {
        calls.push(args)
        return { completed: true, contextInjected: true }
      },
    }),
    batchWindowMs: 0,
  })
  announcements.completed(snapshot)
  await waitFor(() => calls.length === 1)
  assert.match(calls[0][0], /sunny/)
  assert.equal(calls[0][3].latentRefs[0].key, 'task_1/1')
  announcements.close?.()
})
