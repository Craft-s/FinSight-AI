<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Setting, Refresh, CircleCheck, Lock, Connection, ArrowRight } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '../api'
import { useSessionStore } from '../stores'
import { t } from '../i18n'
import { displayTime } from '../utils'
import { configPayload, isVerifiedConfig, PROVIDER_NAMES } from '../llm'
import type { LLMConfig, LLMModel, LLMProvider, LLMStatus, LLMTestResult } from '../types'
import UsageStats from './UsageStats.vue'

const session = useSessionStore()
const status = ref<LLMStatus | null>(null)
const configs = ref<LLMConfig[]>([])
const configListKnown = ref(false)
const provider = ref<LLMProvider>('openai')
const modelId = ref('')
const apiKey = ref('')
const options = ref<Record<string, number | string>>({})
const models = ref<LLMModel[]>([])
const modelListLoaded = ref(false)
const manualId = ref(true)
const loading = ref(false)
const operation = ref('')
const error = ref('')
const statusError = ref('')
const modelError = ref('')
const testResult = ref<LLMTestResult | null>(null)
const optionLabels: Record<string, string> = {max_output_tokens: '最大输出 Token', timeout_seconds: '超时（秒）', temperature: '温度', reasoning_effort: '推理强度'}
let generation = 0
const saved = computed(() => configs.value.find(c => c.provider === provider.value))
const mode = computed({get: () => provider.value === 'ollama' ? 'local' : 'cloud', set: (value: string) => { provider.value = value === 'local' ? 'ollama' : 'openai' }})
const supportedOptions = computed(() => (status.value?.providers.find(p => p.id === provider.value)?.options || []).filter(key => key in optionLabels))
const reasoningEfforts = computed(() => provider.value === 'deepseek' ? ['none', 'low', 'high', 'max'] : ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'])
const temperatureDisabled = computed(() => provider.value === 'deepseek' && options.value.reasoning_effort !== 'none')
const dirty = computed(() => !saved.value || modelId.value.trim() !== saved.value.model_id || !!apiKey.value.trim() || JSON.stringify(options.value) !== JSON.stringify(saved.value.options))
const busy = computed(() => !!operation.value || loading.value)
const canSave = computed(() => !!session.adminToken && !busy.value && !!status.value && (provider.value === 'ollama' ? status.value.ollama_configured : status.value.encryption_ready && (!!apiKey.value.trim() || !!saved.value?.has_api_key)))
const verified = computed(() => !!saved.value && isVerifiedConfig(saved.value))
const connectionState = computed(() => !session.adminToken ? '需要会话令牌' : !configListKnown.value ? '状态未读取' : !saved.value ? '尚未保存' : dirty.value ? '有未保存更改' : verified.value ? '已验证' : '待验证')
function resetDraft() {
  modelId.value = saved.value?.model_id || ''; apiKey.value = ''; options.value = {...saved.value?.options}
  models.value = []; modelListLoaded.value = false; manualId.value = true; modelError.value = ''; error.value = ''; testResult.value = null
}
async function load() {
  const revision = ++generation
  loading.value = true; error.value = ''; statusError.value = ''
  const [capabilities, records] = await Promise.allSettled([api.llmStatus(), session.adminToken ? api.llmConfigs(session.adminToken) : Promise.resolve({items: [] as LLMConfig[]})])
  if (revision !== generation) return
  if (capabilities.status === 'fulfilled') status.value = capabilities.value
  else { status.value = null; statusError.value = errorMessage(capabilities.reason) }
  if (records.status === 'fulfilled') { configs.value = records.value.items; configListKnown.value = !!session.adminToken; resetDraft() }
  else { configs.value = []; configListKnown.value = false; error.value = errorMessage(records.reason) }
  loading.value = false
}
function replaceConfig(config: LLMConfig) { configs.value = [...configs.value.filter(c => c.id !== config.id), config] }
async function saveConfig() {
  if (!canSave.value) return
  const revision = generation
  const body = configPayload(provider.value, modelId.value, apiKey.value, options.value, saved.value)
  operation.value = 'save'; error.value = ''; testResult.value = null
  try {
    const config = await api.saveLLMConfig(body, session.adminToken)
    if (revision !== generation) return
    replaceConfig(config); modelId.value = config.model_id; options.value = {...config.options}; apiKey.value = ''
    models.value = []; modelListLoaded.value = false; manualId.value = true; modelError.value = ''
    ElMessage.success(t('模型配置已保存；更改后需要重新测试连接。'))
  } catch (cause) { if (revision === generation) error.value = errorMessage(cause) }
  finally { apiKey.value = ''; if (revision === generation) operation.value = '' }
}
async function refreshModels() {
  if (!saved.value || dirty.value || busy.value) return
  const revision = generation
  operation.value = 'models'; modelError.value = ''
  try {
    const result = await api.llmModels(saved.value.id, session.adminToken, true)
    if (revision !== generation) return
    models.value = result.items; modelListLoaded.value = true
    if (models.value.length) manualId.value = false
  } catch (cause) { if (revision === generation) { modelError.value = errorMessage(cause); manualId.value = true } }
  finally { if (revision === generation) operation.value = '' }
}
async function testConnection() {
  if (!saved.value || dirty.value || busy.value || !modelId.value.trim()) return
  const revision = generation
  operation.value = 'test'; error.value = ''; testResult.value = null
  try {
    const result = await api.testLLMConfig(saved.value.id, saved.value.version, session.adminToken)
    if (revision !== generation) return
    testResult.value = result; replaceConfig(result.config)
    ElMessage.success(t('连接测试通过，当前版本可用于 AI 概览。'))
  } catch (cause) {
    if (revision === generation) {
      error.value = errorMessage(cause)
      try { configs.value = (await api.llmConfigs(session.adminToken)).items } catch { /* Keep the safe local configuration state when unavailable. */ }
    }
  } finally { if (revision === generation) operation.value = '' }
}
async function activate() {
  if (!saved.value || !verified.value || dirty.value || busy.value) return
  const revision = generation
  operation.value = 'activate'; error.value = ''
  try {
    const result = await api.activateLLMConfig(saved.value.id, saved.value.version, session.adminToken)
    if (revision !== generation) return
    configs.value = configs.value.map(c => ({...c, is_active: false})); replaceConfig(result)
    ElMessage.success(t('已设为默认模型配置。'))
  } catch (cause) { if (revision === generation) error.value = errorMessage(cause) }
  finally { if (revision === generation) operation.value = '' }
}
async function remove() {
  if (!saved.value || busy.value) return
  const revision = generation
  const id = saved.value.id
  const version = saved.value.version
  operation.value = 'delete'; error.value = ''
  try {
    await api.deleteLLMConfig(id, version, session.adminToken)
    if (revision !== generation) return
    configs.value = configs.value.filter(c => c.id !== id); resetDraft()
  } catch (cause) { if (revision === generation) error.value = errorMessage(cause) }
  finally { if (revision === generation) operation.value = '' }
}
function toggleOption(key: string, enabled: string | number | boolean) {
  if (enabled) options.value[key] = ({max_output_tokens: 1024, timeout_seconds: 30, temperature: 0.2, reasoning_effort: 'low'} as Record<string, string | number>)[key]
  else delete options.value[key]
}
watch(provider, resetDraft)
watch(dirty, value => { if (value) testResult.value = null })
watch(() => options.value.reasoning_effort, () => { if (temperatureDisabled.value) delete options.value.temperature })
watch(() => session.adminToken, () => { operation.value = ''; load() })
onMounted(load)
onBeforeUnmount(() => { generation++; apiKey.value = '' })
</script>
<template>
  <section id="model-connections" class="panel settings-panel model-settings">
    <div class="panel-heading model-settings-heading">
      <span class="settings-section-icon"><el-icon><Connection /></el-icon></span>
      <div><h2>{{ t('AI 模型连接') }}</h2><p>{{ t('连接云端模型或部署者的 Ollama，验证后用于结构化财务概览。') }}</p></div>
      <el-button :icon="Refresh" :loading="loading" :disabled="!!operation" :aria-label="t('刷新模型配置')" @click="load">{{ t('刷新模型配置') }}</el-button>
    </div>
    <div class="model-settings-body">
      <el-alert v-if="statusError" :title="t(statusError)" type="error" :closable="false" show-icon />
      <el-alert v-if="!session.adminToken" :title="t('请先输入上方的本地管理员访问令牌。')" :description="t('模型配置和调用需要会话授权，基础财务查询不受影响。')" type="info" :closable="false" show-icon />
      <el-alert v-if="mode === 'cloud' && status && !status.encryption_ready" :title="t('服务端尚未配置有效的加密主密钥。')" :description="t('云端密钥无法保存。请部署者设置 MASTER_ENCRYPTION_KEY；基础财务功能仍可使用。')" type="warning" :closable="false" show-icon />
      <div v-if="loading" class="settings-loading" role="status" :aria-label="t('正在载入模型配置')"><el-skeleton :rows="5" animated /></div>
      <template v-else>
        <div class="connection-toolbar">
          <div class="model-mode"><label>{{ t('运行模式') }}</label><el-radio-group v-model="mode" :disabled="busy" :aria-label="t('运行模式')"><el-radio-button value="cloud">{{ t('云端 API') }}</el-radio-button><el-radio-button value="local">{{ t('Ollama 本地') }}</el-radio-button></el-radio-group></div>
          <span v-if="configListKnown" class="connection-count">{{ t('已保存 {count} 项配置', { count: configs.length }) }}</span>
        </div>
        <div class="provider-status-list" :aria-label="t('已保存的模型配置')">
          <button v-for="name in (['openai', 'deepseek', 'gemini', 'ollama'] as const)" :key="name" type="button" :class="{selected: provider === name}" :disabled="busy" :aria-pressed="provider === name" @click="provider = name">
            <span class="provider-symbol" aria-hidden="true">{{ PROVIDER_NAMES[name].slice(0, 1) }}</span>
            <span class="provider-status-copy"><strong>{{ PROVIDER_NAMES[name] }} <el-icon v-if="configs.some(c => c.provider === name && c.is_active)"><CircleCheck /></el-icon></strong><small :class="{ verified: configs.some(c => c.provider === name && isVerifiedConfig(c)) }">{{ t(!configListKnown ? '状态未读取' : configs.some(c => c.provider === name && isVerifiedConfig(c)) ? '已验证' : configs.some(c => c.provider === name) ? '待验证' : '暂未配置') }}</small></span>
            <span class="provider-selection-dot" aria-hidden="true"></span>
          </button>
        </div>
        <div class="model-workspace">
          <form class="model-form" autocomplete="off" :aria-busy="busy" @submit.prevent="saveConfig">
            <section class="model-setup-step">
              <header class="model-step-heading"><span class="setup-step-number">1</span><div><h3>{{ t('连接凭据') }}</h3><p>{{ t('为所选服务商保存连接信息。') }}</p></div></header>
              <div class="model-step-body">
                <div v-if="mode === 'cloud'" class="model-field"><label for="llm-provider">{{ t('服务商') }}</label><el-select id="llm-provider" v-model="provider" :disabled="busy" :aria-label="t('服务商')"><el-option v-for="name in (['openai', 'deepseek', 'gemini'] as const)" :key="name" :value="name" :label="PROVIDER_NAMES[name]" /></el-select></div>
                <div v-if="mode === 'cloud'" class="model-field">
                  <div class="model-label-row"><label for="llm-key">API Key</label><span v-if="saved?.has_api_key" class="settings-key-badge"><el-icon><Lock /></el-icon>{{ t('已加密保存') }}</span></div>
                  <el-input id="llm-key" v-model="apiKey" type="password" show-password autocomplete="new-password" :maxlength="4096" :disabled="busy || !session.adminToken" :placeholder="t(saved?.has_api_key ? '留空保留现有 Key，填写则替换' : '输入此服务商的 API Key')" />
                  <p class="field-hint">{{ t('密钥仅发送至本应用后端加密保存，不会写入浏览器存储或随读取接口返回。') }}</p>
                </div>
                <div v-else class="ollama-note"><el-alert :title="t(status?.ollama_configured ? '使用部署者配置的 Ollama 服务地址。' : '部署者尚未配置允许的 Ollama 地址。')" :type="status?.ollama_configured ? 'info' : 'warning'" :closable="false" show-icon /><p class="field-hint">{{ t('Ollama 连接由后端发起。云端后端无法访问浏览器所在电脑的 localhost，容器内 localhost 指容器本身。') }} {{ t('为防止任意地址请求，此页面不接受自定义服务 URL。') }}</p></div>
              </div>
            </section>
            <section class="model-setup-step">
              <header class="model-step-heading"><span class="setup-step-number">2</span><div><h3>{{ t('选择模型') }}</h3><p>{{ t('读取可用列表，或使用精确模型 ID。') }}</p></div></header>
              <div class="model-step-body">
                <div class="model-discovery-row"><span>{{ t('服务商模型列表') }}</span><el-button :icon="Refresh" :disabled="!saved || dirty || busy" :loading="operation === 'models'" :title="t(!saved ? '请先保存配置，再读取模型列表。' : dirty ? '有未保存的更改，请先保存再刷新模型、测试或启用。' : '刷新模型')" @click="refreshModels">{{ t('刷新模型') }}</el-button></div>
                <div class="model-field">
                  <div class="model-label-row"><label for="llm-model">{{ t('模型 ID') }}</label><el-switch v-model="manualId" :active-text="t('手动输入')" :aria-label="t('手动输入')" :disabled="busy || !models.length" /></div>
                  <el-input v-if="manualId" id="llm-model" v-model="modelId" :disabled="busy || !session.adminToken" :placeholder="t('填写服务商提供的精确模型 ID')" :maxlength="200" />
                  <el-select v-else id="llm-model" v-model="modelId" filterable :disabled="busy" :placeholder="t('选择模型')" :aria-label="t('选择模型')"><el-option v-for="model in models" :key="model.id" :value="model.id" :label="model.display_name || model.id" :disabled="model.supports_text_generation === false" /></el-select>
                  <p class="field-hint">{{ t('首次可先保存 Key，再刷新模型列表；也可以直接填写精确模型 ID。模型列表不保证账户具有调用权限。') }}</p>
                </div>
                <el-alert v-if="modelError" :title="t(modelError)" :description="t('模型列表不可用时，仍可手动填写模型 ID 后保存并测试。')" type="warning" :closable="false" show-icon />
                <p v-else-if="modelListLoaded && !models.length" class="model-empty-list" role="status">{{ t('服务商未返回模型列表，可继续手动输入模型 ID。') }}</p>
                <p v-if="models.length" class="model-list-count" role="status"><el-icon><CircleCheck /></el-icon>{{ t('已获取 {count} 个模型；未知能力以实际连接测试为准。', {count: models.length}) }}</p>
                <el-collapse v-if="supportedOptions.length" class="model-advanced"><el-collapse-item :title="t('高级选项（可选）')" name="options"><p class="field-hint">{{ t('默认使用安全的输出与超时限制，其余参数遵循服务商默认值。修改后必须重新验证。') }}</p><p v-if="provider === 'deepseek'" class="field-hint">{{ t('DeepSeek 温度仅在推理强度设为 none 时可用。') }}</p><div v-for="key in supportedOptions" :key="key" class="advanced-option"><el-checkbox :model-value="key in options" :disabled="busy || (key === 'temperature' && temperatureDisabled)" @change="(value: string | number | boolean) => toggleOption(key, value)">{{ t(optionLabels[key]) }}</el-checkbox><template v-if="key in options"><el-select v-if="key === 'reasoning_effort'" v-model="options[key]" :disabled="busy" :aria-label="t('推理强度')"><el-option v-for="value in reasoningEfforts" :key="value" :value="value" :label="value" /></el-select><el-input-number v-else v-model="options[key] as number" :disabled="busy" :min="key === 'temperature' ? 0 : key === 'timeout_seconds' ? 5 : 16" :max="key === 'temperature' ? 2 : key === 'timeout_seconds' ? 120 : 8192" :step="key === 'temperature' ? 0.1 : 1" :precision="key === 'temperature' ? 1 : 0" :aria-label="t(optionLabels[key])" /></template></div></el-collapse-item></el-collapse>
              </div>
            </section>
            <section class="model-setup-step verification-step">
              <header class="model-step-heading"><span class="setup-step-number">3</span><div><h3>{{ t('验证与启用') }}</h3><p>{{ t('保存配置后进行真实连接测试，通过后再设为默认。') }}</p></div></header>
              <div class="model-step-body">
                <p v-if="dirty && saved" class="unsaved-hint" role="status">{{ t('有未保存的更改，请先保存再刷新模型、测试或启用。') }}</p>
                <el-alert v-if="error" :title="t(error)" type="error" :closable="false" show-icon />
                <el-alert v-if="testResult" :title="t('连接测试通过，当前版本可用于 AI 概览。')" :description="t('实际文本调用耗时 {ms} ms。测试可能产生少量模型用量。', {ms: testResult.duration_ms})" type="success" :closable="false" show-icon />
                <UsageStats v-if="testResult" :usage="testResult.usage" />
                <div class="settings-actions model-actions"><el-button :type="!saved || dirty || !modelId.trim() ? 'primary' : 'default'" native-type="submit" :disabled="!canSave" :loading="operation === 'save'">{{ t('保存配置') }}</el-button><el-button :type="saved && !dirty && modelId.trim() && !verified ? 'primary' : 'default'" :disabled="!saved || dirty || busy || !modelId.trim()" :loading="operation === 'test'" @click="testConnection">{{ t('测试连接') }}</el-button><el-button :type="verified && !dirty && !saved?.is_active ? 'primary' : 'default'" :disabled="!verified || dirty || busy || saved?.is_active" :loading="operation === 'activate'" @click="activate">{{ t('设为默认') }}</el-button></div>
                <p v-if="operation === 'test'" class="connection-test-progress" role="status"><el-icon class="is-loading"><Refresh /></el-icon>{{ t('正在执行真实连接测试，请稍候…') }}</p>
                <p class="field-hint">{{ t('连接测试会执行一次小型真实模型调用；保存配置和读取模型列表不等于测试通过。') }}</p>
              </div>
            </section>
          </form>
          <aside class="connection-summary" :aria-label="t('当前连接状态')">
            <div class="connection-summary-heading"><span class="tiny-label">{{ t('当前连接') }}</span><el-icon><Setting /></el-icon></div>
            <h3>{{ PROVIDER_NAMES[provider] }}</h3><span class="connection-state-badge" :class="{ verified: verified && !dirty, pending: dirty && saved }">{{ t(connectionState) }}</span>
            <dl class="connection-summary-meta"><dt>{{ t('运行模式') }}</dt><dd>{{ t(mode === 'cloud' ? '云端 API' : 'Ollama 本地') }}</dd><dt>{{ t('模型 ID') }}</dt><dd class="configured-model-id">{{ saved?.model_id || t('尚未选择模型') }}</dd></dl>
            <div v-if="saved" class="config-state"><span>{{ t('配置版本') }} {{ saved.version }}</span><span v-if="saved.is_active" class="small-badge">{{ t('默认配置') }}</span><p v-if="saved.last_tested_at">{{ t('最近测试') }}<br>{{ displayTime(saved.last_tested_at) }}</p></div>
            <ol class="connection-steps"><li :class="{ complete: !!saved }"><span><el-icon v-if="saved"><CircleCheck /></el-icon><template v-else>1</template></span><div>{{ t('保存连接信息') }}<small>{{ t(saved ? '配置已保存' : '等待保存') }}</small></div></li><li :class="{ complete: !!saved?.model_id }"><span><el-icon v-if="saved?.model_id"><CircleCheck /></el-icon><template v-else>2</template></span><div>{{ t('选择模型') }}<small>{{ t(saved?.model_id ? '已保存模型 ID' : '读取列表或手动输入') }}</small></div></li><li :class="{ complete: verified && !dirty }"><span><el-icon v-if="verified && !dirty"><CircleCheck /></el-icon><template v-else>3</template></span><div>{{ t('测试连接') }}<small>{{ t(verified && !dirty ? '已验证' : '等待验证') }}</small></div></li></ol>
            <p class="connection-security-note"><el-icon><Lock /></el-icon><span>{{ t('凭据加密保存，模型修改后重新验证。') }}</span></p>
            <el-popconfirm v-if="saved" :title="t('删除此模型配置及已保存的 Key？')" :confirm-button-text="t('删除')" :cancel-button-text="t('取消')" @confirm="remove"><template #reference><button type="button" class="delete-connection" :disabled="busy" :aria-busy="operation === 'delete'"><span>{{ t('删除配置') }}</span><el-icon v-if="operation === 'delete'" class="is-loading"><Refresh /></el-icon><el-icon v-else><ArrowRight /></el-icon></button></template></el-popconfirm>
          </aside>
        </div>
      </template>
    </div>
  </section>
</template>

