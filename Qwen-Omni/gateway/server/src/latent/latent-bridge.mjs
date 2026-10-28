import { FileLatentStore } from './latent-store.mjs'
import { isLatentTaskId, latentMarker } from './latent-ref.mjs'

/**
 * Gateway side of latent communication between the background Agent and the
 * frontend model. Disabled by default; when disabled every method returns its
 * input unchanged so existing text delivery is untouched.
 */
export class LatentBridge {
  constructor({ store = null, enabled = false, logger = null } = {}) {
    this.enabled = enabled === true && Boolean(store)
    this.store = store
    this.logger = logger
  }

  /**
   * Prefixes the backend instruction with the Task's correlation marker.
   * latent-proxy reads and strips the marker before the model sees the prompt.
   */
  markInstruction(taskId, instruction) {
    const text = String(instruction ?? '')
    if (!this.enabled || !text.trim() || !isLatentTaskId(taskId)) return instruction
    return `${latentMarker(taskId)}\n${text}`
  }

  /**
   * Adds latentRef to a backend outcome when the store holds a final latent
   * for the Task. A store failure never fails the Task; it only drops the ref.
   */
  async attachToOutcome(taskId, outcome) {
    if (!this.enabled || !outcome || typeof outcome !== 'object') return outcome
    try {
      const latentRef = await this.store.latest(taskId)
      return latentRef ? { ...outcome, latentRef } : outcome
    } catch (error) {
      this.logger?.warn?.('latent.store_read_failed', {
        taskId,
        error: error?.message || String(error),
      })
      return outcome
    }
  }
}

export function createLatentBridge(config = {}, { logger = null } = {}) {
  if (config.latentEnabled !== true || !config.latentStoreDirectory) {
    return new LatentBridge()
  }
  return new LatentBridge({
    enabled: true,
    store: new FileLatentStore({ directory: config.latentStoreDirectory }),
    logger,
  })
}
