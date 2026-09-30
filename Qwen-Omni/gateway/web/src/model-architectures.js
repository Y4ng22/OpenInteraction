export const MODEL_ARCHITECTURES = Object.freeze([
  {
    id: 'qwen-omni',
    provider: 'dashscope',
    label: 'Qwen-Omni',
    detail: 'Qwen3.8 Realtime · qwen-plus',
  },
  {
    id: 'minicpm',
    provider: 'minicpm-o',
    label: 'MiniCPM',
    detail: 'MiniCPM-o 4.5 · 原有实时服务',
  },
  {
    id: 'self-developed',
    provider: null,
    label: '自研模型',
    detail: 'InteractFormer · Bridge 原型',
  },
])

// "Configured" is deliberately not called "available": only an established
// provider connection proves that a route currently works.
export function modelArchitectureOptions({
  health = null,
  selectedProvider = 'dashscope',
  connectionState = 'connecting',
} = {}) {
  const gatewayReady = health?.ok === true
  const providers = new Map((health?.realtimeProviders || []).map(item => [item.key, item]))
  return MODEL_ARCHITECTURES.map(route => {
    if (!route.provider) {
      return { ...route, selectable: false, status: 'development' }
    }
    const configured = providers.get(route.provider)?.configured === true
    if (!gatewayReady) {
      return { ...route, selectable: false, status: 'gateway-offline' }
    }
    if (!configured) {
      return { ...route, selectable: false, status: 'not-configured' }
    }
    if (route.provider !== selectedProvider) {
      return { ...route, selectable: true, status: 'standby' }
    }
    const status = connectionState === 'connected'
      ? 'available'
      : connectionState === 'unavailable'
        ? 'connection-error'
        : 'connecting'
    return { ...route, selectable: true, status }
  })
}
