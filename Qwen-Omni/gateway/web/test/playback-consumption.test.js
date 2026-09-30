import test from 'node:test'
import assert from 'node:assert/strict'
import { consumedPlaybackMs, scheduledPlaybackMs } from '../src/realtime/playback-consumption.js'

test('interruption counts rendered audio, excluding queued audio and pauses', () => {
  const segments = [{ start: 10, end: 11 }, { start: 11, end: 12 }]
  assert.equal(consumedPlaybackMs(segments, 10.25), 250)
  assert.equal(scheduledPlaybackMs(segments), 2000)
  assert.equal(consumedPlaybackMs(segments, 11.5), 1500)
  assert.equal(consumedPlaybackMs(segments, 13), 2000)
})
