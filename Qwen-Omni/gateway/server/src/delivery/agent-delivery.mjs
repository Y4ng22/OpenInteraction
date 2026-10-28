import { randomUUID } from 'node:crypto'

export const AgentDeliveryMode = Object.freeze({
  HANDLE: 'handle',
  CONTEXT: 'context',
  RESPOND: 'respond',
  INTERRUPT: 'interrupt',
})

const MODES = new Set(Object.values(AgentDeliveryMode))

function clean(value) {
  return String(value || '').trim()
}

/**
 * Provider-neutral input for the frontend Agent.
 *
 * Domain runtimes own event policy and serialization. Realtime adapters only
 * receive this value after the Gateway has decided whether the event should be
 * handled internally, added to context, answered, or allowed to interrupt.
 */
export function createAgentDelivery({
  id = `delivery_${randomUUID()}`,
  causeEventId = null,
  mode = AgentDeliveryMode.HANDLE,
  origin = 'gateway',
  text,
  correlation = {},
  presentation = {},
  latentRefs = [],
} = {}) {
  const normalizedMode = clean(mode)
  const normalizedText = clean(text)
  if (!MODES.has(normalizedMode)) {
    throw new TypeError(`invalid AgentDelivery mode: ${normalizedMode || '<empty>'}`)
  }
  if (normalizedMode !== AgentDeliveryMode.HANDLE && !normalizedText) {
    throw new TypeError('model-visible AgentDelivery requires text')
  }
  const presentationInstructions = clean(presentation.instructions)
  const presentationAllowsTools = presentation.allowTools === true
  const contextTiming = presentation.contextTiming === 'immediate'
    ? 'immediate'
    : 'response'
  // LatentRefs are validated where Tasks accept backend outcomes.
  const normalizedLatentRefs = Array.isArray(latentRefs)
    ? latentRefs.filter(ref => ref && typeof ref === 'object')
    : []
  return Object.freeze({
    id: clean(id) || `delivery_${randomUUID()}`,
    ...(clean(causeEventId) ? { causeEventId: clean(causeEventId) } : {}),
    mode: normalizedMode,
    origin: clean(origin) || 'gateway',
    text: normalizedText,
    correlation: Object.freeze({ ...correlation }),
    // Background latents for frontends that declare latentInjection. The text
    // remains authoritative; frontends without the capability ignore this.
    ...(normalizedLatentRefs.length
      ? { latentRefs: Object.freeze(normalizedLatentRefs) }
      : {}),
    ...(presentationInstructions || presentationAllowsTools || contextTiming === 'immediate'
      ? {
          presentation: Object.freeze({
            instructions: presentationInstructions,
            allowTools: presentationAllowsTools,
            contextTiming,
          }),
        }
      : {}),
  })
}

export function isModelVisibleDelivery(delivery) {
  return delivery?.mode !== AgentDeliveryMode.HANDLE
}
