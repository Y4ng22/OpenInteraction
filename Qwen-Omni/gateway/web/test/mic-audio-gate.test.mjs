import assert from 'node:assert/strict'
import test from 'node:test'
import { createMicAudioGate } from '../src/realtime/mic-audio-gate.js'

const BATCH = 1280 // 80 ms @ 16 kHz
const PRE_ROLL = 3840 // 240 ms @ 16 kHz
const HANGOVER = 9600 // 600 ms @ 16 kHz
const FRAME = 100

function frame(count, amplitude) {
  return new Float32Array(count).fill(amplitude)
}

function feed(gate, count, amplitude) {
  const samples = frame(count, amplitude)
  for (let offset = 0; offset < count; offset += FRAME) {
    gate.feed(samples.subarray(offset, offset + FRAME))
  }
}

function recordingGate(options = {}) {
  const emitted = []
  const states = []
  const gate = createMicAudioGate({
    batchMs: 80,
    preRollMs: 240,
    hangoverMs: 600,
    openThreshold: 0.015,
    closeThreshold: 0.010,
    noiseRatio: 5,
    silenceKeepAliveMs: 0,
    ...options,
    onBatch: batch => emitted.push(batch),
    onStateChange: state => states.push(state),
  })
  return { gate, emitted, states }
}

function joined(emitted) {
  const output = new Float32Array(
    emitted.reduce((sum, batch) => sum + batch.length, 0),
  )
  let offset = 0
  for (const batch of emitted) {
    output.set(batch, offset)
    offset += batch.length
  }
  return output
}

test('a noisy quiet tail is followed by enough guaranteed silence to finish cloud VAD', () => {
  const { gate, emitted } = recordingGate({ endpointSilenceMs: 800 })
  feed(gate, 16_000, 0.2)
  feed(gate, HANGOVER, 0.009)
  assert.equal(gate.state, 'closed')
  const output = joined(emitted)
  assert.equal(output.length, 16_000 + HANGOVER + 12_800)
  assert.ok(output.subarray(-12_800).every(sample => sample === 0))
  const count = emitted.length
  feed(gate, 64_000, 0)
  assert.equal(emitted.length, count) // No continuous idle upload congestion.
})

test('纯静音不输出任何音频，门保持关闭', () => {
  const { gate, emitted, states } = recordingGate()
  for (let index = 0; index < 200; index += 1) gate.feed(frame(FRAME, 0))
  assert.equal(emitted.length, 0)
  assert.equal(gate.state, 'closed')
  assert.deepEqual(states, [])
})

test('语音起始不丢字：开瞬间先输出 pre-roll 再输出语音', () => {
  const { gate, emitted } = recordingGate()
  // 0.005 低于自适应阈值（噪声底 0.005 → 阈值 0.025），视为静音。
  feed(gate, 5000, 0.005)
  gate.feed(frame(100, 0.2))
  assert.equal(gate.state, 'open')
  gate.flush()
  const output = joined(emitted)
  // 输出 = 3840 个 pre-roll 样本（0.005）+ 100 个语音样本（0.2）。
  assert.equal(output.length, PRE_ROLL + 100)
  for (let index = 0; index < PRE_ROLL; index += 1) {
    assert.equal(output[index], Math.fround(0.005))
  }
  for (let index = PRE_ROLL; index < output.length; index += 1) {
    assert.equal(output[index], Math.fround(0.2))
  }
})

test('连续语音按 1280 样本批量对齐输出，跨不规则帧边界不丢样本', () => {
  const { gate, emitted } = recordingGate()
  gate.feed(frame(100, 0.2))
  const irregular = new Float32Array(5000).fill(0.2)
  let offset = 0
  const sizes = [42, 43]
  let index = 0
  while (offset < irregular.length) {
    const size = sizes[index % sizes.length]
    gate.feed(irregular.subarray(offset, offset + size))
    offset += size
    index += 1
  }
  gate.flush()
  assert.equal(emitted.reduce((sum, batch) => sum + batch.length, 0), 5100)
  for (const batch of emitted.slice(0, -1)) assert.equal(batch.length, BATCH)
  assert.equal(emitted.at(-1).length, 5100 - 3 * BATCH)
})

test('慢关尾音：静音短于 hangover 不关闭，超时后残留批量已 flush', () => {
  const { gate, emitted, states } = recordingGate()
  gate.feed(frame(100, 0.2))
  feed(gate, 8000, 0) // 500ms < hangover 600ms
  assert.equal(gate.state, 'closing')
  assert.equal(emitted.length, Math.floor(8100 / BATCH))
  feed(gate, 1600, 0) // 总计 9600 = hangover
  assert.equal(gate.state, 'closed')
  assert.equal(emitted.reduce((sum, batch) => sum + batch.length, 0), 100 + HANGOVER)
  // 关闭时残留不足一批的量（9700 - 7×1280 = 740）也已 flush。
  assert.equal(emitted.at(-1).length, 740)
  const before = emitted.length
  gate.feed(frame(FRAME, 0))
  assert.equal(emitted.length, before)
  assert.deepEqual(states, ['open', 'closing', 'closed'])
})

test('hangover 中能量回升回到 open，且不重复输出 pre-roll', () => {
  const { gate, emitted, states } = recordingGate()
  gate.feed(frame(100, 0.2))
  feed(gate, 4800, 0) // 进入 closing（300ms 静音）
  assert.equal(gate.state, 'closing')
  const countBeforeResume = emitted.reduce((sum, batch) => sum + batch.length, 0)
  gate.feed(frame(100, 0.2)) // 能量回升
  assert.equal(gate.state, 'open')
  const countAfterResume = emitted.reduce((sum, batch) => sum + batch.length, 0)
  // 回升瞬间只追加语音帧本身，不重放 pre-roll。
  assert.equal(countAfterResume - countBeforeResume, 0)
  assert.deepEqual(states, ['open', 'closing', 'open'])
})

test('flush 输出残留并回到 closed，之后静音不再输出', () => {
  const { gate, emitted } = recordingGate()
  gate.feed(frame(100, 0.2))
  gate.flush()
  assert.equal(gate.state, 'closed')
  assert.equal(emitted.length, 1)
  assert.equal(emitted[0].length, 100)
  const before = emitted.length
  feed(gate, 5000, 0)
  assert.equal(emitted.length, before)
})

test('reset 清空 pre-roll 与状态', () => {
  const { gate, emitted } = recordingGate()
  gate.feed(frame(100, 0.3))
  assert.equal(gate.state, 'open')
  gate.reset()
  assert.equal(gate.state, 'closed')
  feed(gate, 100, 0.005)
  gate.feed(frame(100, 0.2))
  feed(gate, 2000, 0.2)
  gate.flush()
  // 第一个输出样本来自 reset 之后的 pre-roll（0.005），而非 reset 前的 0.3。
  assert.equal(emitted[0][0], Math.fround(0.005))
})

test('噪声底自适应：低幅噪声抬高阈值，慢速静默后回落', () => {
  const { gate } = recordingGate()
  // 0.012 噪声持续 20000 样本后噪声底≈0.012，阈值≈0.06。
  feed(gate, 20000, 0.012)
  assert.equal(gate.state, 'closed')
  // 0.04 高于绝对下限 0.015，但低于自适应阈值 → 不打开。
  gate.feed(frame(100, 0.04))
  assert.equal(gate.state, 'closed')
  // 0.15 超过 0.06 阈值 → 打开。
  gate.feed(frame(100, 0.15))
  assert.equal(gate.state, 'open')
  // 长时间数字静默让噪声底缓慢回落（τ≈312ms），阈值回到 0.015。
  feed(gate, 30000, 0)
  assert.equal(gate.state, 'closed')
  gate.feed(frame(100, 0.05))
  assert.equal(gate.state, 'open')
})

test('迟滞：介于关闭与打开阈值之间的信号不反复切换状态', () => {
  const { gate, states } = recordingGate()
  gate.feed(frame(100, 0.2))
  assert.equal(gate.state, 'open')
  // 0.012 ≥ closeThreshold(0.010)：open 态不进入 closing。
  feed(gate, 10000, 0.012)
  assert.equal(gate.state, 'open')
  // 从 closed 看，0.012 < openThreshold(0.015)：不打开。
  gate.reset()
  feed(gate, 1000, 0.012)
  assert.equal(gate.state, 'closed')
  // reset 本身会产生一次 closed 状态事件。
  assert.deepEqual(states, ['open', 'closed'])
})

test('keep-alive：closed 态按间隔发送小段全零批；关闭参数后不再发送', () => {
  const keepAlive = recordingGate({ silenceKeepAliveMs: 4000 })
  feed(keepAlive.gate, 64000, 0) // 4s → 一个 20ms 全零批
  assert.equal(keepAlive.emitted.length, 1)
  assert.equal(keepAlive.emitted[0].length, 320)
  assert.ok(keepAlive.emitted[0].every(sample => sample === 0))
  const silent = recordingGate({ silenceKeepAliveMs: 0 })
  feed(silent.gate, 64000, 0)
  assert.equal(silent.emitted.length, 0)
})

test('样本守恒：无 pre-roll 时输出总样本等于开门后输入总样本', () => {
  const { gate, emitted } = recordingGate({ preRollMs: 0 })
  const chunkSizes = [100, 42, 43, 100, 7, 100]
  const amplitudes = [0.2, 0.2, 0.2, 0, 0, 0.2]
  let counted = 0
  let everOpened = false
  for (let index = 0; index < chunkSizes.length; index += 1) {
    const chunk = frame(chunkSizes[index], amplitudes[index])
    gate.feed(chunk)
    if (everOpened || ['open', 'closing'].includes(gate.state)) {
      counted += chunkSizes[index]
    }
    everOpened = everOpened || gate.state === 'open'
  }
  // 尾音静音直到关闭：整段都会被输出。
  feed(gate, HANGOVER, 0)
  counted += HANGOVER
  const output = emitted.reduce((sum, batch) => sum + batch.length, 0)
  assert.equal(gate.state, 'closed')
  assert.equal(output, counted)
})

test('空帧与零长度输入安全', () => {
  const { gate, emitted } = recordingGate()
  gate.feed(new Float32Array(0))
  gate.feed(null)
  assert.equal(emitted.length, 0)
  assert.equal(gate.state, 'closed')
})

test('缺少 onBatch 时抛错', () => {
  assert.throws(() => createMicAudioGate({}), /onBatch is required/)
})
