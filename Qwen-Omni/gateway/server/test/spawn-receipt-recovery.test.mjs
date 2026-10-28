import assert from 'node:assert/strict'
import test from 'node:test'
import { TaskManager } from '../src/task/task-manager.mjs'
import { ToolCallHandler } from '../src/frontend/tools/tool-call-handler.mjs'
import { TurnTranscripts } from '../src/frontend/tools/turn-transcripts.mjs'

// A realtime provider rejects a deferred spawn receipt when its function call
// id has already expired (seen with DashScope when the user barges in).
function harness({ rejectReceipt = false, appendFails = false } = {}) {
  const manager = new TaskManager()
  const contexts = []
  const outputs = []
  const frontend = {
    sendFunctionOutput: async (...args) => {
      outputs.push(args)
      if (rejectReceipt) throw new Error(`invalid_request_error: Unknown function call id: ${args[0]}`)
    },
    ensureResponse: async () => {},
    appendUserContext: async text => {
      if (appendFails) throw new Error('socket closed')
      contexts.push(text)
    },
  }
  const handler = new ToolCallHandler({
    taskManager: manager,
    ownerId: 'owner',
    sessionId: 'voice',
    transcripts: new TurnTranscripts({ waitMs: 5 }),
    getFrontend: () => frontend,
    getTurnId: () => 'turn-one',
    getTurnGeneration: () => 1,
    backendRuntime: {
      run: () => new Promise(() => {}),
      cancel: async taskId => ({ taskId, state: 'cancelled' }),
    },
  })
  const spawn = () => handler.handle({
    call_id: 'call-spawn',
    name: 'spawn_thinking',
    arguments: JSON.stringify({ objective: '调研新能源汽车行业新闻' }),
  }, { turnId: 'turn-one', turnGeneration: 1 })
  return { manager, contexts, outputs, spawn }
}

test('a rejected spawn receipt gives the model the task id as context', async () => {
  const kit = harness({ rejectReceipt: true })
  await assert.rejects(kit.spawn(), /Unknown function call id/)
  const [task] = kit.manager.list({ ownerId: 'owner' })
  assert.ok(task, 'the task keeps running after the receipt is lost')
  assert.equal(kit.contexts.length, 1)
  assert.match(kit.contexts[0], /^<background_task_accepted>/)
  assert.match(kit.contexts[0], new RegExp(`task_id=${task.id}\\n`))
  assert.match(kit.contexts[0], /objective=调研新能源汽车行业新闻/)
})

test('a delivered spawn receipt adds no extra context', async () => {
  const kit = harness()
  await kit.spawn()
  assert.equal(kit.outputs[0][1].status, 'accepted')
  assert.deepEqual(kit.contexts, [])
})

test('a failing recovery never hides the original receipt error', async () => {
  const kit = harness({ rejectReceipt: true, appendFails: true })
  await assert.rejects(kit.spawn(), /Unknown function call id/)
  assert.equal(kit.manager.list({ ownerId: 'owner' }).length, 1)
})
