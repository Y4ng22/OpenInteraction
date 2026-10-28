/**
 * Latent communication contract shared by the Gateway and latent-proxy.
 *
 * The Gateway never carries tensors. It marks a backend instruction with the
 * Task id, latent-proxy writes the background model's latent into a store
 * under that id, and the Gateway forwards only a LatentRef to a frontend that
 * declares the latentInjection capability. The wire formats here must stay in
 * sync with Qwen-Omni/latent-proxy/latent_proxy/markers.py and store.py.
 */

export const LATENT_REF_VERSION = 1

// Task ids become one path segment in the store, so the alphabet excludes
// separators and a leading dot.
const TASK_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/
const MARKER_PATTERN = /<latent_task id="([A-Za-z0-9][A-Za-z0-9_.-]{0,127})"\s*\/>/g
const DTYPES = new Set(['float32', 'float16', 'bfloat16'])

function clean(value) {
  return String(value ?? '').trim()
}

export function isLatentTaskId(value) {
  return TASK_ID_PATTERN.test(clean(value))
}

export function latentMarker(taskId) {
  const id = clean(taskId)
  if (!isLatentTaskId(id)) {
    throw new TypeError(`invalid latent task id: ${id || '<empty>'}`)
  }
  return `<latent_task id="${id}"/>`
}

/** Returns the id of the last marker in text, or null. */
export function lastLatentMarker(text) {
  let found = null
  for (const match of String(text ?? '').matchAll(MARKER_PATTERN)) found = match[1]
  return found
}

function positiveInteger(value) {
  const number = Number(value)
  return Number.isInteger(number) && number > 0 ? number : null
}

/**
 * Validates a LatentRef. Returns a frozen copy, or null when the value is not
 * a usable reference; callers then fall back to the text delivery.
 */
export function normalizeLatentRef(value) {
  if (!value || typeof value !== 'object') return null
  const taskId = clean(value.taskId)
  const seq = positiveInteger(value.seq)
  const tokens = positiveInteger(value.tokens)
  const dim = positiveInteger(value.dim)
  const dtype = clean(value.dtype)
  const key = clean(value.key)
  if (
    Number(value.version) !== LATENT_REF_VERSION
    || !isLatentTaskId(taskId)
    || !seq || !tokens || !dim
    || !DTYPES.has(dtype)
    || key !== `${taskId}/${seq}`
  ) return null
  const layer = Number(value.layer)
  return Object.freeze({
    version: LATENT_REF_VERSION,
    taskId,
    seq,
    key,
    final: value.final === true,
    model: clean(value.model),
    layer: Number.isInteger(layer) ? layer : -1,
    tokens,
    dim,
    dtype,
    createdAtMs: Number(value.createdAtMs) || 0,
  })
}

export function normalizeLatentRefs(values) {
  if (!Array.isArray(values)) return []
  return values.map(normalizeLatentRef).filter(Boolean)
}
