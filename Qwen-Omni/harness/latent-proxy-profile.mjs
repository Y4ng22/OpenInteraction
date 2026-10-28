// Routes the Harness qwen-plus provider through latent-proxy. The template's
// DashScope route keeps its key, models and options; only its baseURL changes.
export const DASHSCOPE_BASE_URL_LINE = 'baseURL: https://dashscope.aliyuncs.com/compatible-mode/v1'

export function harnessProfileThroughLatentProxy(template, proxyBaseUrl) {
  const url = new URL(String(proxyBaseUrl || '').trim())
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error(`LATENT_PROXY_BASE_URL must be http(s): ${proxyBaseUrl}`)
  }
  const occurrences = template.split(DASHSCOPE_BASE_URL_LINE).length - 1
  if (occurrences !== 1) {
    throw new Error(
      `cordis.patch.yml must contain exactly one "${DASHSCOPE_BASE_URL_LINE}" line, found ${occurrences}`,
    )
  }
  return template.replace(DASHSCOPE_BASE_URL_LINE, `baseURL: ${url.href.replace(/\/$/, '')}`)
}
