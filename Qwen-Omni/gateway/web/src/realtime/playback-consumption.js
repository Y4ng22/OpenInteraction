// Web Audio's clock is the authority for what reached the output device.
// Generated transcript length and queued PCM are deliberately not used here.
export function consumedPlaybackMs(segments = [], currentTime = 0) {
  return Math.round(segments.reduce((total, segment) => {
    const duration = Math.max(0, segment.end - segment.start)
    const played = Math.max(0, Math.min(duration, currentTime - segment.start))
    return total + played * 1000
  }, 0))
}

export function scheduledPlaybackMs(segments = []) {
  return Math.round(segments.reduce((total, segment) => (
    total + Math.max(0, segment.end - segment.start) * 1000
  ), 0))
}
