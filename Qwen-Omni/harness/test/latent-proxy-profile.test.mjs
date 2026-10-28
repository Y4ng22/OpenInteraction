import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  DASHSCOPE_BASE_URL_LINE,
  harnessProfileThroughLatentProxy,
} from '../latent-proxy-profile.mjs'

const template = readFileSync(new URL('../cordis.patch.yml', import.meta.url), 'utf8')

test('the shipped profile routes only the DashScope baseURL through latent-proxy', () => {
  const routed = harnessProfileThroughLatentProxy(template, 'http://127.0.0.1:8765/v1/')
  assert.equal(routed.includes(DASHSCOPE_BASE_URL_LINE), false)
  assert.match(routed, /baseURL: http:\/\/127\.0\.0\.1:8765\/v1\n/)
  assert.equal(
    routed.replace('baseURL: http://127.0.0.1:8765/v1', DASHSCOPE_BASE_URL_LINE),
    template,
  )
})

test('invalid proxy URLs and unexpected templates are refused', () => {
  assert.throws(() => harnessProfileThroughLatentProxy(template, 'not a url'))
  assert.throws(() => harnessProfileThroughLatentProxy(template, 'ftp://host/v1'), /http\(s\)/)
  assert.throws(() => harnessProfileThroughLatentProxy('id: acp\n', 'http://127.0.0.1:8765/v1'), /exactly one/)
})
