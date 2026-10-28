import { assertBackendPort } from './backend-port.mjs'
import { backendInstructionFromWork } from './backend-work-input.mjs'
import { LatentBridge } from '../latent/latent-bridge.mjs'

function clean(value) {
  return String(value || '').trim()
}

/**
 * Protocol-neutral application facade for one configured backend.
 *
 * It translates the Gateway's accepted Task context into BackendPort calls.
 * Prompt formats, Session routing, and delegation topology belong to adapters.
 */
export class BackendWorkRuntime {
  constructor({ backend, latent = null } = {}) {
    this.backend = assertBackendPort(backend, {
      name: 'BackendWorkRuntime backend',
    })
    // A disabled bridge returns instructions and outcomes unchanged.
    this.latent = latent || new LatentBridge()
  }

  async run(input, options = {}) {
    const taskId = clean(options.taskId)
    const work = {
      id: taskId,
      ownerId: clean(options.ownerId),
      instruction: input?.instruction,
      objective: input?.objective,
      inputParts: input?.inputParts || [],
      ...(['isolated', 'task'].includes(options.continuity)
        ? { continuity: options.continuity }
        : {}),
    }
    work.instruction = this.latent.markInstruction(
      taskId,
      backendInstructionFromWork(work),
    )
    const outcome = await this.backend.submit(work, {
      signal: options.signal,
      onEvent: options.onEvent,
    })
    return this.latent.attachToOutcome(taskId, outcome)
  }

  /**
   * Run system-owned utility work without placing it in the user's persistent
   * coordinator context. Adapters must also bypass any optional conversation
   * continuity (for example, ACP Sessions or reusable A2A contexts).
   */
  runIsolated(input, options = {}) {
    return this.run(input, { ...options, continuity: 'isolated' })
  }

  cancel(taskId, options = {}) {
    return this.backend.cancel(taskId, options)
  }

  steer(taskId, message, options = {}) {
    if (typeof this.backend.steer !== 'function') {
      throw new Error('Backend does not support live task steering')
    }
    return this.backend.steer(taskId, message, options)
  }

  status(taskId, options = {}) {
    return this.backend.status(taskId, options)
  }

  respondInput(taskId, inputRequestId, response, options = {}) {
    return this.backend.respondInput(
      taskId,
      inputRequestId,
      response,
      options,
    )
  }
}
