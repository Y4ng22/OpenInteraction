export const WEBSOCKET_AUDIO_BUFFER_LIMIT = 512 * 1024
export const WEBSOCKET_AUDIO_DROP_HIGH_WATER = 64 * 1024
export const WEBSOCKET_AUDIO_RESUME_BUFFER = 16 * 1024
export const WEBSOCKET_CONTROL_BUFFER_LIMIT = 1024 * 1024
export const WEBSOCKET_MESSAGE_LIMIT = 20 * 1024 * 1024

const failedSockets = new WeakSet()
const pausedAudioSockets = new WeakSet()

// Do not add an application queue in front of ws's own queue. Most providers
// fail closed on overflow; Qwen can opt into dropping stale live microphone
// frames while preserving the ordered control channel and current session.
export function sendBoundedWebSocket(ws, data, {
  audio = false,
  dropCongestedAudio = false,
  onAudioDrop,
  onFailure,
} = {}) {
  if (ws?.readyState !== 1 || failedSockets.has(ws)) return false
  const size = Buffer.byteLength(data)
  const bufferedBytes = Number(ws.bufferedAmount) || 0
  const limit = audio ? WEBSOCKET_AUDIO_BUFFER_LIMIT : WEBSOCKET_CONTROL_BUFFER_LIMIT
  const audioDropLimit = audio && dropCongestedAudio
    ? WEBSOCKET_AUDIO_DROP_HIGH_WATER : limit
  const dropAudio = () => {
    try {
      onAudioDrop?.({ bufferedBytes, messageBytes: size, limit: audioDropLimit })
    } catch {
      // Telemetry must not interrupt the live stream.
    }
  }
  const fail = (code, error) => {
    if (failedSockets.has(ws)) return
    failedSockets.add(ws)
    try {
      onFailure?.({ code, bufferedBytes, messageBytes: size, limit, error })
    } catch {
      // Diagnostics must not prevent transport cleanup or escape callbacks.
    } finally {
      ws.terminate()
    }
  }
  // A large control message (e.g. an attachment) may be sent to an empty queue.
  // Subsequent messages cannot grow that queue further. Audio never gets this
  // exception: its budget bounds both memory and stale-speech latency.
  const budget = audio ? limit : Math.max(limit, size)
  // Live microphone audio has an expiration time: replaying seconds of queued
  // speech after a slow network recovers is worse than skipping those frames.
  // Keep the Qwen session and its control messages alive while the transport
  // drains; other providers retain the existing fail-closed behavior.
  if (audio && dropCongestedAudio && size <= WEBSOCKET_MESSAGE_LIMIT) {
    if (pausedAudioSockets.has(ws) && bufferedBytes > WEBSOCKET_AUDIO_RESUME_BUFFER) {
      dropAudio()
      return false
    }
    pausedAudioSockets.delete(ws)
    if (bufferedBytes + size > audioDropLimit) {
      pausedAudioSockets.add(ws)
      dropAudio()
      return false
    }
  }
  if (size > WEBSOCKET_MESSAGE_LIMIT || bufferedBytes + size > budget) {
    fail('websocket_backpressure')
    return false
  }
  try {
    ws.send(data, error => { if (error) fail('websocket_send_failed', error) })
    return !failedSockets.has(ws)
  } catch (error) {
    fail('websocket_send_failed', error)
    return false
  }
}
