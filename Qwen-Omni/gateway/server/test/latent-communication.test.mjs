import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { BackendWorkRuntime } from '../src/backend/backend-work-runtime.mjs'
import { createLatentBridge, LatentBridge } from '../src/latent/latent-bridge.mjs'
import {
  lastLatentMarker,
  latentMarker,
  normalizeLatentRef,
  normalizeLatentRefs,
} from '../src/latent/latent-ref.mjs'
import { FileLatentStore } from '../src/latent/latent-store.mjs'

// Writes a store entry exactly as latent-proxy/latent_proxy/store.py does.
async function writeEntry(root, taskId, seq, {
  final = true,
  tokens = 2,
  dim = 3,
  dtype = 'float32',
  payloadBytes = tokens * dim * 4,
  manifest = {},
} = {}) {
  const directory = join(root, taskId)
  await mkdir(directory, { recursive: true })
  await writeFile(join(directory, `${seq}.bin`), Buffer.alloc(payloadBytes))
  await writeFile(join(directory, `${seq}.json`), JSON.stringify({
    version: 1,
    task_id: taskId,
    seq,
    final,
    model: 'stub',
    layer: -1,
    tokens,
    dim,
    dtype,
    payload: `${seq}.bin`,
    byte_length: tokens * dim * 4,
    created_at_ms: 1700000000000 + seq,
    ...manifest,
  }))
}

async function tempStore(t) {
  const root = await mkdtemp(join(tmpdir(), 'latent-store-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  return root
}

function validRef(overrides = {}) {
  return {
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
    ...overrides,
  }
}

function fakeBackend(outcome = { content: 'done' }) {
  const submitted = []
  return {
    submitted,
    describe: () => ({}),
    start: async () => {},
    health: async () => ({}),
    submit: async work => {
      submitted.push(work)
      return outcome
    },
    status: async () => ({}),
    cancel: async () => ({}),
    respondAuthorization: async () => ({}),
    respondInput: async () => ({}),
    subscribe: () => () => {},
    close: async () => {},
  }
}

test('latent markers round-trip and reject ids that are not one path segment', () => {
  assert.equal(latentMarker('task_12'), '<latent_task id="task_12"/>')
  assert.equal(lastLatentMarker(`${latentMarker('task_1')}\nA ${latentMarker('task_2')} B`), 'task_2')
  assert.equal(lastLatentMarker('no marker'), null)
  for (const bad of ['', '../x', 'a/b', '.hidden', 'x'.repeat(129)]) {
    assert.throws(() => latentMarker(bad), /invalid latent task id/)
  }
})

test('normalizeLatentRef accepts the contract and rejects anything else', () => {
  const ref = normalizeLatentRef(validRef())
  assert.equal(Object.isFrozen(ref), true)
  assert.equal(ref.key, 'task_1/1')
  for (const broken of [
    validRef({ version: 2 }),
    validRef({ taskId: '../etc' }),
    validRef({ key: 'task_1/2' }),
    validRef({ tokens: 0 }),
    validRef({ dtype: 'int8' }),
    null,
    'task_1/1',
  ]) {
    assert.equal(normalizeLatentRef(broken), null)
  }
  assert.deepEqual(normalizeLatentRefs([validRef(), validRef({ dim: -1 })]).length, 1)
  assert.deepEqual(normalizeLatentRefs(undefined), [])
})

test('FileLatentStore returns the newest final latent and skips partial or broken entries', async t => {
  const root = await tempStore(t)
  const store = new FileLatentStore({ directory: root })
  assert.equal(await store.latest('task_1'), null)
  assert.equal(await store.latest('../outside'), null)

  await writeEntry(root, 'task_1', 1)
  await writeEntry(root, 'task_1', 2, { final: false })
  await writeEntry(root, 'task_1', 3, { payloadBytes: 5 })
  await writeEntry(root, 'task_1', 4, { manifest: { task_id: 'task_9' } })
  await writeFile(join(root, 'task_1', '5.json'), '{not json')

  const ref = await store.latest('task_1')
  assert.equal(ref.seq, 1)
  assert.equal(ref.final, true)
  assert.equal(ref.key, 'task_1/1')
  assert.equal((await store.latest('task_1', { finalOnly: false })).seq, 2)

  await writeEntry(root, 'task_1', 10, { tokens: 4, dim: 8, dtype: 'float16', payloadBytes: 4 * 8 * 2 })
  const newest = await store.latest('task_1')
  assert.equal(newest.seq, 10)
  assert.equal(newest.dtype, 'float16')
})

test('a disabled LatentBridge leaves instructions and outcomes untouched', async () => {
  const bridge = new LatentBridge()
  const outcome = { content: 'x' }
  assert.equal(bridge.enabled, false)
  assert.equal(bridge.markInstruction('task_1', 'do it'), 'do it')
  assert.equal(await bridge.attachToOutcome('task_1', outcome), outcome)
  assert.equal(createLatentBridge({}).enabled, false)
  assert.equal(createLatentBridge({ latentEnabled: true }).enabled, false)
  assert.equal(createLatentBridge({ latentEnabled: false, latentStoreDirectory: '/tmp/x' }).enabled, false)
})

test('an enabled LatentBridge marks instructions and attaches the final latent', async t => {
  const root = await tempStore(t)
  const bridge = createLatentBridge({ latentEnabled: true, latentStoreDirectory: root })
  assert.equal(bridge.enabled, true)
  assert.equal(bridge.markInstruction('task_1', 'look it up'), '<latent_task id="task_1"/>\nlook it up')
  assert.equal(bridge.markInstruction('bad/id', 'look it up'), 'look it up')
  assert.equal(bridge.markInstruction('task_1', '  '), '  ')

  const outcome = { content: 'answer' }
  assert.equal(await bridge.attachToOutcome('task_1', outcome), outcome)
  await writeEntry(root, 'task_1', 1)
  const attached = await bridge.attachToOutcome('task_1', outcome)
  assert.equal(attached.content, 'answer')
  assert.equal(attached.latentRef.key, 'task_1/1')
})

test('store read failures drop the latentRef without failing the task', async () => {
  const warnings = []
  const bridge = new LatentBridge({
    enabled: true,
    store: { latest: async () => { throw new Error('disk gone') } },
    logger: { warn: (event, details) => warnings.push({ event, details }) },
  })
  const outcome = { content: 'answer' }
  assert.equal(await bridge.attachToOutcome('task_1', outcome), outcome)
  assert.equal(warnings[0].event, 'latent.store_read_failed')
})

test('BackendWorkRuntime is unchanged without a bridge and marks work when latent is enabled', async t => {
  const plainBackend = fakeBackend()
  const plain = new BackendWorkRuntime({ backend: plainBackend })
  assert.deepEqual(
    await plain.run({ objective: 'weather' }, { taskId: 'task_1', ownerId: 'owner', continuity: 'task' }),
    { content: 'done' },
  )
  assert.equal(plainBackend.submitted[0].instruction, 'weather')
  assert.equal(plainBackend.submitted[0].continuity, 'task')

  const root = await tempStore(t)
  await writeEntry(root, 'task_2', 1)
  const latentBackend = fakeBackend()
  const runtime = new BackendWorkRuntime({
    backend: latentBackend,
    latent: createLatentBridge({ latentEnabled: true, latentStoreDirectory: root }),
  })
  const outcome = await runtime.run({ objective: 'weather' }, { taskId: 'task_2', ownerId: 'owner' })
  assert.equal(latentBackend.submitted[0].instruction, '<latent_task id="task_2"/>\nweather')
  assert.equal(latentBackend.submitted[0].objective, 'weather')
  assert.equal(outcome.content, 'done')
  assert.equal(outcome.latentRef.taskId, 'task_2')
})
