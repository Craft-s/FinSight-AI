import { describe, expect, it } from 'vitest'
import { configPayload, isVerifiedConfig } from './llm'
import type { LLMConfig } from './types'
import { locale, metricLabel, setLocale } from './i18n'

const config: LLMConfig = {id: 'config-1', provider: 'openai', model_id: 'account-model', version: 4, verified_version: 4, is_verified: true, is_active: true, has_api_key: true, options: {}, last_tested_at: null, created_at: '', updated_at: ''}

describe('model configuration state', () => {
  it('rejects stale or incomplete verification before allowing model use', () => {
    expect(isVerifiedConfig(config)).toBe(true)
    expect(isVerifiedConfig({...config, version: 5})).toBe(false)
    expect(isVerifiedConfig({...config, is_verified: false})).toBe(false)
    expect(isVerifiedConfig({...config, model_id: ''})).toBe(false)
  })
  it('omits a blank key so editing a saved model does not delete the secret', () => {
    expect(configPayload('openai', ' next-model ', '  ', {}, config)).toEqual({id: 'config-1', version: 4, provider: 'openai', model_id: 'next-model', options: {}})
  })
  it('excludes a lingering cloud key when switching to local inference', () => {
    expect(configPayload('ollama', 'local-model', 'cloud-secret', {})).not.toHaveProperty('api_key')
  })
  it('does not copy read-side or unrecognized configuration properties into writes', () => {
    const record = {...config, encrypted_key: 'ciphertext', base_url: 'https://untrusted.example'}
    const body = configPayload('openai', record.model_id, '', {max_output_tokens: 512}, record)
    expect(body).not.toHaveProperty('base_url')
    expect(body).not.toHaveProperty('encrypted_key')
    expect(body).not.toHaveProperty('is_verified')
    expect(body.options).toEqual({max_output_tokens: 512})
  })
})

describe('overview fact labels', () => {
  it.each([
    ['revenue_yoy', '营业收入 YoY', 'Revenue YoY change', '营业收入 同比'],
    ['net_income_yoy', '净利润 YoY', 'Net income YoY change', '净利润 同比'],
  ])('localizes %s from its metric key rather than using the Chinese API fallback', (key, fallback, english, chinese) => {
    const previous = locale.value
    try {
      setLocale('en-US')
      expect(metricLabel(key, fallback)).toBe(english)
      expect(metricLabel(key, fallback)).not.toMatch(/[\u4e00-\u9fff]/)
      setLocale('zh-CN')
      expect(metricLabel(key, fallback)).toBe(chinese)
    } finally { setLocale(previous) }
  })
})
