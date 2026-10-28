import assert from 'node:assert/strict'
import { once } from 'node:events'
import { setTimeout as delay } from 'node:timers/promises'
import test from 'node:test'
import { WebSocketServer } from 'ws'
import { config } from '../src/core/config.mjs'
import { RealtimeFrontend } from '../src/voice/realtime-provider.mjs'
import { dashscopeProvider } from '../src/voice/providers/dashscope.mjs'

// Reproduces a live DashScope failure: a progress update was requested the
// moment the user started speaking, the user's speech cancelled it, DashScope
// never acknowledged the cancel, and the single-slot recovery reconnected the
// session, dropping the user's utterance.
const MODEL = 'qwen3.8-omni-flash-realtime'

function voicedPcm(ms = 80, amplitude = 8000) {
  const samples = new Int16Array(16 * ms)
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = Math.round(Math.sin(index / 5) * amplitude)
  }
  return Buffer.from(samples.buffer).toString('base64')
}

async function waitFor(predicate, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs
  while (!predicate()) {
    assert.ok(Date.now() < deadline, 'Timed out waiting for a protocol event')
    await delay(10)
  }
}

async function harness(t, { proactiveUserFloorMs } = {}) {
  const values = {
    audioModel: MODEL, audioVoice: '', dashscopeApiKey: 'test-key',
    audioRealtimeBaseUrl: '',
  }
  const previous = Object.fromEntries(Object.keys(values).map(key => [key, config[key]]))
  Object.assign(config, values)
  t.after(() => Object.assign(config, previous))
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await once(server, 'listening')
  config.audioRealtimeBaseUrl = `ws://127.0.0.1:${server.address().port}/realtime`
  const received = []
  let socket
  let connections = 0
  let closed = 0
  const emit = event => socket.send(JSON.stringify(event))
  server.on('connection', ws => {
    socket = ws
    connections += 1
    ws.on('close', () => { closed += 1 })
    ws.on('message', raw => {
      const event = JSON.parse(raw)
      received.push(event)
      if (event.type === 'session.update') emit({ type: 'session.updated', session: event.session })
      if (event.type === 'conversation.item.create') {
        emit({ type: 'conversation.item.created', item: { ...event.item, id: `server-${received.length}` } })
      }
      // Like the live service: response.cancel for a request it never started
      // gets no acknowledgement at all.
    })
    emit({ type: 'session.created' })
  })
  const frontend = new RealtimeFrontend({
    provider: dashscopeProvider,
    onEvent: () => {},
    onError: () => {},
    responseStartTimeoutMs: 2000,
    responseCancelGraceMs: 200,
    ...(proactiveUserFloorMs === undefined ? {} : { proactiveUserFloorMs }),
  })
  t.after(() => {
    frontend.close()
    for (const client of server.clients) client.terminate()
    server.close()
  })
  await frontend.connect()
  // The input runtime cancels gateway responses when the provider reports speech.
  const userStartsSpeaking = () => {
    emit({ type: 'input_audio_buffer.speech_started', item_id: 'user-speech', audio_start_ms: 0 })
    return waitFor(() => frontend.inputSpeechActive).then(() => frontend.cancel())
  }
  const reply = id => {
    emit({ type: 'response.created', response: { id, status: 'in_progress' } })
    emit({ type: 'response.audio.delta', response_id: id, delta: Buffer.alloc(480).toString('base64') })
    emit({ type: 'response.done', response: { id, status: 'completed', output: [] } })
  }
  const responseCreates = () => received.filter(event => event.type === 'response.create')
  return {
    frontend, received, emit, reply, userStartsSpeaking, responseCreates,
    connections: () => connections,
    closed: () => closed,
  }
}

test('a progress update waits while the user is speaking and never forces a reconnect', async t => {
  const h = await harness(t, { proactiveUserFloorMs: 300 })
  h.frontend.appendAudio(voicedPcm())
  const outcome = h.frontend.injectDelivery('任务进展：正在检索', 'progress', { taskId: 'task_1' })
  await delay(100)
  assert.equal(h.responseCreates().length, 0, 'must not request speech over the user')
  await h.userStartsSpeaking()
  assert.equal((await outcome).cancelled, true)
  await delay(400)
  assert.equal(h.received.some(event => event.type === 'response.cancel'), false)
  assert.equal(h.closed(), 0, 'the session must stay connected')
  assert.equal(h.connections(), 1)
})

test('a progress update speaks after the user has been quiet for the floor time', async t => {
  const h = await harness(t, { proactiveUserFloorMs: 300 })
  const startedAt = Date.now()
  const outcome = h.frontend.injectDelivery('任务进展：已完成一半', 'progress', { taskId: 'task_1' })
  await waitFor(() => h.responseCreates().length === 1)
  assert.ok(Date.now() - startedAt >= 280, 'waits for the floor before speaking')
  h.reply('progress-reply')
  assert.equal((await outcome).completed, true)
})

test('user-driven replies are not delayed by the floor', async t => {
  const h = await harness(t, { proactiveUserFloorMs: 1000 })
  h.frontend.appendAudio(voicedPcm())
  const startedAt = Date.now()
  const outcome = h.frontend.injectDelivery('工具结果', 'agent', {})
  await waitFor(() => h.responseCreates().length === 1)
  assert.ok(Date.now() - startedAt < 500)
  h.reply('agent-reply')
  assert.equal((await outcome).completed, true)
})

test('without a configured floor the previous behavior is unchanged', async t => {
  const h = await harness(t)
  h.frontend.appendAudio(voicedPcm())
  const outcome = h.frontend.injectDelivery('任务进展', 'progress', { taskId: 'task_1' })
  await waitFor(() => h.responseCreates().length === 1, 500)
  h.reply('progress-reply')
  assert.equal((await outcome).completed, true)
})
