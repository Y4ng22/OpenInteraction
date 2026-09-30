// Builds a single-channel 16 kHz PCM16 WAV with utterances placed on a fixed
// timeline, for Chromium's --use-file-for-fake-audio-capture flag.
//
// Each utterance is synthesized with the macOS `say` command, converted to
// WAV with afconvert, peak-normalized to 0.9 (the synthesizer can be quiet,
// and the energy gate must reliably open), then spliced into the timeline.
//
// Chromium LOOPS the fake-capture file from the beginning once it ends, so
// the trailing silence must extend past the whole scenario horizon —
// otherwise the utterances repeat and produce ghost turns.
import { execFileSync } from 'node:child_process'
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export const SAMPLE_RATE = 16_000
const NORMALIZE_PEAK = 0.9

export function pickVoice() {
  let voices = ''
  try {
    voices = execFileSync('say', ['-v', '?'], { encoding: 'utf8' })
  } catch {
    return 'Tingting'
  }
  const lines = voices.split('\n')
  const preferred = ['Tingting', 'Sinji', 'Meijia'].map(name => (
    lines.find(line => line.trim().startsWith(name) && line.includes('zh_CN'))
  ))
  const zh = preferred.find(Boolean)?.trim().split(/\s+/)[0]
  if (zh) return zh
  const any = lines.find(line => line.includes('zh_CN'))
  if (any) return any.trim().split(/\s+/)[0]
  throw new Error('未找到 macOS 中文语音；请安装中文语音包或指定语音')
}

function sayToPcm(text, voice) {
  const dir = mkdtempSync(join(tmpdir(), 'qwene2e-voice-'))
  try {
    const aiff = join(dir, 'seg.aiff')
    const wav = join(dir, 'seg.wav')
    execFileSync('say', ['-v', voice, '-o', aiff, String(text)])
    execFileSync('afconvert', ['-f', 'WAVE', '-d', 'LEI16@' + SAMPLE_RATE, '-c', '1', aiff, wav])
    const { samples } = readWav(readFileSync(wav))
    // Peak-normalize so the energy gate opens reliably regardless of the
    // synthesizer's default volume.
    let peak = 0
    for (const sample of samples) peak = Math.max(peak, Math.abs(sample))
    if (peak > 0) {
      const gain = NORMALIZE_PEAK / peak
      for (let index = 0; index < samples.length; index += 1) {
        samples[index] *= gain
      }
    }
    return samples
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

export function readWav(buffer) {
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength)
  const ascii = (offset, length) => (
    String.fromCharCode(...buffer.subarray(offset, offset + length))
  )
  if (ascii(0, 4) !== 'RIFF' || ascii(8, 4) !== 'WAVE') {
    throw new Error('不是有效的 WAV 文件')
  }
  const rate = view.getUint32(24, true)
  const channels = view.getUint16(22, true)
  const bits = view.getUint16(34, true)
  let offset = 12
  let dataOffset = -1
  let dataLength = 0
  while (offset + 8 <= buffer.length) {
    const id = ascii(offset, 4)
    const size = view.getUint32(offset + 4, true)
    if (id === 'data') {
      dataOffset = offset + 8
      dataLength = size
      break
    }
    offset += 8 + size + (size % 2)
  }
  if (dataOffset < 0) throw new Error('WAV 缺少 data chunk')
  const count = Math.floor(dataLength / (bits / 8))
  const samples = new Float32Array(count)
  for (let index = 0; index < count; index += 1) {
    samples[index] = view.getInt16(dataOffset + index * 2, true) / 0x8000
  }
  return { samples, sampleRate: rate, channels }
}

export function writeWav(filePath, samples, sampleRate = SAMPLE_RATE) {
  const buffer = Buffer.alloc(44 + samples.length * 2)
  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(36 + samples.length * 2, 4)
  buffer.write('WAVE', 8)
  buffer.write('fmt ', 12)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20) // PCM
  buffer.writeUInt16LE(1, 22) // mono
  buffer.writeUInt32LE(sampleRate, 24)
  buffer.writeUInt32LE(sampleRate * 2, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36)
  buffer.writeUInt32LE(samples.length * 2, 40)
  for (let index = 0; index < samples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[index]))
    buffer.writeInt16LE(Math.round(clamped * 0x7fff), 44 + index * 2)
  }
  writeFileSync(filePath, buffer)
}

/**
 * utterances: [{ text, atSec }]; tailSeconds pads silence after the last one.
 * Returns the WAV path.
 */
export function buildTimelineWav({
  filePath,
  utterances = [],
  tailSeconds = 120,
  voice,
} = {}) {
  const resolvedVoice = voice || pickVoice()
  const clips = utterances.map(item => ({
    atSec: Math.max(0, Number(item.atSec) || 0),
    samples: sayToPcm(String(item.text || ''), resolvedVoice),
  }))
  const totalSeconds = clips.reduce(
    (end, clip) => Math.max(end, clip.atSec + clip.samples.length / SAMPLE_RATE),
    tailSeconds,
  )
  const output = new Float32Array(Math.ceil(totalSeconds * SAMPLE_RATE))
  for (const clip of clips) {
    const start = Math.round(clip.atSec * SAMPLE_RATE)
    for (let index = 0; index < clip.samples.length; index += 1) {
      const at = start + index
      if (at < output.length) output[at] += clip.samples[index]
    }
  }
  writeWav(filePath, output)
  return filePath
}
