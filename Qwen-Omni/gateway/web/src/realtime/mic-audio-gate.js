/**
 * Energy-gates a live microphone stream and batches its PCM before sending.
 *
 * The browser microphone is always on, so an ungated client streams roughly
 * 375 small messages per second (~80 KiB/s) even during silence. On slow or
 * cross-region links that backlog never drains, and the gateway's congestion
 * backstop then drops every expired frame — including real speech. Gating
 * sends only speech (with a short pre-roll so onsets are not clipped, and a
 * trailing silence so server-side VAD still hears the end of the utterance),
 * and batching cuts the message count by another order of magnitude.
 *
 * All timing advances by sample count, never by wall clock, so the state
 * machine is deterministic and unit-testable. Pure logic: feed() receives
 * mono Float32 samples at one fixed rate; onBatch() receives aligned batches
 * of the same rate.
 */
export function createMicAudioGate({
  sampleRate = 16_000,
  endpointSilenceMs = 0,
  batchMs = 80,
  preRollMs = 240,
  hangoverMs = 600,
  openThreshold = 0.015,
  closeThreshold = 0.010,
  noiseRatio = 5,
  noiseFloorFloor = 0.003,
  noiseFloorDecay = 0.9998,
  noiseCeil = 0.25,
  silenceKeepAliveMs = 4000,
  keepAliveBatchMs = 20,
  onBatch,
  onStateChange = () => {},
} = {}) {
  const batchSamples = Math.max(1, Math.round(sampleRate * Number(batchMs) / 1000) || 1)
  const preRollSamples = Math.max(0, Math.round(sampleRate * Number(preRollMs) / 1000) || 0)
  const hangoverSamples = Math.max(0, Math.round(sampleRate * Number(hangoverMs) / 1000) || 0)
  const endpointSilenceSamples = Math.max(0, Math.round(sampleRate * Number(endpointSilenceMs) / 1000) || 0)
  const keepAliveSamples = Math.max(0, Math.round(sampleRate * Number(silenceKeepAliveMs) / 1000) || 0)
  const keepAliveBatchSamples = Math.max(
    1,
    Math.min(batchSamples, Math.round(sampleRate * Number(keepAliveBatchMs) / 1000) || 1),
  )
  const openFloor = Math.max(0, Number(openThreshold) || 0)
  const closeFloor = Math.max(0, Number(closeThreshold) || 0)
  const ratio = Math.max(1, Number(noiseRatio) || 1)
  const floorFloor = Math.max(0, Number(noiseFloorFloor) || 0)
  const decay = Math.min(1, Math.max(0, Number(noiseFloorDecay) || 0))
  const ceil = Math.max(0, Number(noiseCeil) || 0)
  if (typeof onBatch !== 'function') throw new TypeError('onBatch is required')
  if (typeof onStateChange !== 'function') throw new TypeError('onStateChange is required')
  let state = 'closed'
  let noiseFloor = 0
  let quietSamples = 0
  let sinceKeepAlive = 0
  // Rolling window of the most recent samples, kept fresh while the gate is
  // closed so opening can prepend the pre-roll without a gap.
  const ring = new Float32Array(preRollSamples)
  let ringCount = 0
  let ringPos = 0
  const batch = new Float32Array(batchSamples)
  let batchFill = 0

  const setState = next => {
    if (next === state) return
    state = next
    onStateChange(state)
  }

  const append = samples => {
    let offset = 0
    while (offset < samples.length) {
      const take = Math.min(batchSamples - batchFill, samples.length - offset)
      batch.set(samples.subarray(offset, offset + take), batchFill)
      batchFill += take
      offset += take
      if (batchFill === batchSamples) emitBatch(true)
    }
  }

  const emitBatch = force => {
    if (!force && batchFill < batchSamples) return
    if (batchFill > 0) {
      onBatch(batch.slice(0, batchFill))
      batchFill = 0
    }
  }

  const writeRing = samples => {
    if (!preRollSamples) return
    for (let index = 0; index < samples.length; index += 1) {
      ring[ringPos] = samples[index]
      ringPos = (ringPos + 1) % preRollSamples
      if (ringCount < preRollSamples) ringCount += 1
    }
  }

  const prependPreRoll = () => {
    if (!ringCount) return
    const start = (ringPos - ringCount + preRollSamples) % preRollSamples
    const preRoll = new Float32Array(ringCount)
    for (let index = 0; index < ringCount; index += 1) {
      preRoll[index] = ring[(start + index) % preRollSamples]
    }
    append(preRoll)
  }

  const frameRms = samples => {
    let sum = 0
    for (let index = 0; index < samples.length; index += 1) {
      sum += samples[index] * samples[index]
    }
    return Math.sqrt(sum / Math.max(1, samples.length))
  }

  const openThresholdFor = () => Math.max(
    Math.max(noiseFloor, floorFloor) * ratio,
    openFloor,
  )

  return {
    get state() {
      return state
    },

    get sampleRate() {
      return sampleRate
    },

    feed(samples) {
      if (!samples?.length) return
      const rms = frameRms(samples)
      const threshold = openThresholdFor()
      const loud = rms >= threshold
      if (state === 'closed' && loud) {
        setState('open')
        quietSamples = 0
        sinceKeepAlive = 0
        // Pre-roll must contain only samples from BEFORE the opening frame:
        // the ring is written after the prepend on this transition.
        prependPreRoll()
        writeRing(samples)
        append(samples)
        return
      }
      writeRing(samples)
      if (state === 'open') {
        if (rms < closeFloor) {
          setState('closing')
          // The silent frame that triggers the hangover counts toward it.
          quietSamples = samples.length
        }
        append(samples)
        return
      }
      if (state === 'closing') {
        if (loud) {
          setState('open')
          quietSamples = 0
          append(samples)
          return
        }
        quietSamples += samples.length
        append(samples)
        if (quietSamples >= hangoverSamples) {
          emitBatch(true)
          // The quiet microphone tail may still contain noise that cloud VAD
          // labels as speech. Supply a bounded, guaranteed-silent tail before
          // pausing idle uploads; sparse keepalives cannot finish a VAD turn.
          if (endpointSilenceSamples) {
            append(new Float32Array(endpointSilenceSamples))
            emitBatch(true)
          }
          setState('closed')
        }
        return
      }
      // Closed and quiet: track the noise floor as a slow leaky integrator,
      // so one stray knock cannot instantly raise the threshold and deafen
      // the gate until the floor decays back down.
      const retention = decay ** samples.length
      noiseFloor = (
        noiseFloor * retention
        + Math.min(rms, ceil) * (1 - retention)
      )
      if (keepAliveSamples) {
        // Idle keepalive only. It cannot finish a speech turn: cloud VAD
        // measures audio samples, so endpointSilenceMs supplies that tail.
        sinceKeepAlive += samples.length
        if (sinceKeepAlive >= keepAliveSamples) {
          sinceKeepAlive = 0
          append(new Float32Array(keepAliveBatchSamples))
          emitBatch(true)
        }
      }
    },

    // Emits the pending remainder (open/closing) and returns to closed.
    flush() {
      emitBatch(true)
      setState('closed')
    },

    reset() {
      setState('closed')
      noiseFloor = 0
      quietSamples = 0
      sinceKeepAlive = 0
      ringCount = 0
      ringPos = 0
      batchFill = 0
    },
  }
}
