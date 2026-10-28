import { readdir, readFile, stat } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import {
  isLatentTaskId,
  LATENT_REF_VERSION,
  normalizeLatentRef,
} from './latent-ref.mjs'

const DTYPE_BYTES = Object.freeze({ float32: 4, float16: 2, bfloat16: 2 })
const MANIFEST_PATTERN = /^([1-9][0-9]{0,8})\.json$/

/**
 * Read-only view of the directory store written by latent-proxy.
 *
 * Layout: <directory>/<taskId>/<seq>.json is the manifest and
 * <directory>/<taskId>/<seq>.bin the row-major [tokens, dim] tensor. The proxy
 * writes the payload before the manifest, both through a rename, so a visible
 * manifest always describes a complete payload. The Gateway only reads
 * manifests; frontend model servers read payloads by LatentRef.key.
 */
export class FileLatentStore {
  constructor({ directory } = {}) {
    if (!String(directory || '').trim()) {
      throw new TypeError('FileLatentStore requires a directory')
    }
    this.directory = resolve(directory)
  }

  /**
   * Returns the newest LatentRef for a Task, or null when the Task has none.
   * finalOnly skips partial latents written during tool-call rounds.
   */
  async latest(taskId, { finalOnly = true } = {}) {
    if (!isLatentTaskId(taskId)) return null
    const taskDirectory = join(this.directory, taskId)
    let names
    try {
      names = await readdir(taskDirectory)
    } catch (error) {
      if (error?.code === 'ENOENT') return null
      throw error
    }
    const seqs = names
      .map(name => MANIFEST_PATTERN.exec(name)?.[1])
      .filter(Boolean)
      .map(Number)
      .sort((left, right) => right - left)
    for (const seq of seqs) {
      const ref = await this.readManifest(taskDirectory, taskId, seq)
      if (ref && (!finalOnly || ref.final)) return ref
    }
    return null
  }

  async readManifest(taskDirectory, taskId, seq) {
    let manifest
    try {
      manifest = JSON.parse(await readFile(join(taskDirectory, `${seq}.json`), 'utf8'))
    } catch {
      return null
    }
    if (manifest?.task_id !== taskId || Number(manifest?.seq) !== seq) return null
    const ref = normalizeLatentRef({
      version: manifest.version,
      taskId,
      seq,
      key: `${taskId}/${seq}`,
      final: manifest.final,
      model: manifest.model,
      layer: manifest.layer,
      tokens: manifest.tokens,
      dim: manifest.dim,
      dtype: manifest.dtype,
      createdAtMs: manifest.created_at_ms,
    })
    if (!ref || ref.version !== LATENT_REF_VERSION) return null
    if (manifest.payload !== `${seq}.bin`) return null
    const expectedBytes = ref.tokens * ref.dim * DTYPE_BYTES[ref.dtype]
    try {
      const payload = await stat(join(taskDirectory, manifest.payload))
      if (payload.size !== expectedBytes) return null
    } catch {
      return null
    }
    return ref
  }
}
