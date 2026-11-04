import type { LLMConfig, LLMProvider, LLMConfigInput } from './types'

export const PROVIDER_NAMES: Record<LLMProvider, string> = { openai: 'OpenAI', deepseek: 'DeepSeek', gemini: 'Gemini', ollama: 'Ollama' }
export function isVerifiedConfig(config: LLMConfig): boolean {
  return config.is_verified && config.verified_version === config.version && !!config.model_id
}
/** Whitelist the payload. API keys are sent once, never included in preferences. */
export function configPayload(provider: LLMProvider, modelId: string, key: string, options: Record<string, number | string>, saved?: LLMConfig): LLMConfigInput {
  return { ...(saved ? {id: saved.id, version: saved.version} : {}), provider, model_id: modelId.trim(), ...(key.trim() && provider !== 'ollama' ? {api_key: key.trim()} : {}), options: {...options} }
}
