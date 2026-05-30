<template>
  <teleport to="body">
    <div v-if="visible" class="settings-backdrop" @click.self="close">
      <div class="settings-panel">
        <div class="panel-header">
          <span class="panel-title">{{ $t('settings.title') }}</span>
          <button class="close-btn" @click="close">×</button>
        </div>

        <div class="panel-body">
          <!-- Provider 切换 -->
          <div class="section">
            <label class="section-label">{{ $t('settings.provider') }}</label>
            <div class="toggle-group">
              <button
                :class="['toggle-btn', { active: provider === 'network' }]"
                @click="provider = 'network'"
              >{{ $t('settings.network') }}</button>
              <button
                :class="['toggle-btn', { active: provider === 'local' }]"
                @click="provider = 'local'"
              >{{ $t('settings.local') }}</button>
            </div>
          </div>

          <!-- 云端 API 配置 -->
          <div class="section" v-show="provider === 'network'">
            <div class="section-header">
              <span class="section-icon">☁</span>
              <span>{{ $t('settings.networkConfig') }}</span>
            </div>
            <div class="field">
              <label>{{ $t('settings.apiKey') }}</label>
              <input v-model="network.api_key" type="password" :placeholder="$t('settings.apiKeyPlaceholder')" />
            </div>
            <div class="field">
              <label>{{ $t('settings.baseUrl') }}</label>
              <input v-model="network.base_url" type="text" placeholder="https://api.deepseek.com" />
            </div>
            <div class="field">
              <label>{{ $t('settings.modelName') }}</label>
              <input v-model="network.model_name" type="text" placeholder="deepseek-chat" />
            </div>
            <button class="test-btn" @click="testConnection('network')" :disabled="testing">
              <span v-if="testing && testTarget === 'network'" class="spin">⟳</span>
              {{ $t('settings.testConnection') }}
            </button>
            <div v-if="testResult && testTarget === 'network'" :class="['test-result', testResult.ok ? 'ok' : 'fail']">
              {{ testResult.message }}
            </div>
          </div>

          <!-- 本地 API 配置 -->
          <div class="section" v-show="provider === 'local'">
            <div class="section-header">
              <span class="section-icon">🏠</span>
              <span>{{ $t('settings.localConfig') }}</span>
            </div>
            <div class="field">
              <label>{{ $t('settings.baseUrl') }}</label>
              <input v-model="local.base_url" type="text" placeholder="http://localhost:11434/v1" />
            </div>
            <div class="field">
              <label>{{ $t('settings.modelName') }}</label>
              <input v-model="local.model_name" type="text" placeholder="qwen2.5:7b" />
            </div>
            <div class="field">
              <label>{{ $t('settings.apiKey') }} <span class="optional">{{ $t('settings.optional') }}</span></label>
              <input v-model="local.api_key" type="password" :placeholder="$t('settings.localApiKeyPlaceholder')" />
            </div>
            <button class="test-btn" @click="testConnection('local')" :disabled="testing">
              <span v-if="testing && testTarget === 'local'" class="spin">⟳</span>
              {{ $t('settings.testConnection') }}
            </button>
            <div v-if="testResult && testTarget === 'local'" :class="['test-result', testResult.ok ? 'ok' : 'fail']">
              {{ testResult.message }}
            </div>
          </div>
        </div>

        <div class="panel-footer">
          <div v-if="saveMessage" :class="['save-msg', saveOk ? 'ok' : 'fail']">{{ saveMessage }}</div>
          <button class="save-btn" @click="saveSettings" :disabled="saving">
            <span v-if="saving" class="spin">⟳</span>
            {{ $t('settings.save') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api/index.js'

const { t } = useI18n()

const props = defineProps({
  visible: { type: Boolean, default: false }
})
const emit = defineEmits(['close'])

const provider = ref('network')
const network = reactive({ api_key: '', base_url: '', model_name: '' })
const local = reactive({ api_key: '', base_url: '', model_name: '' })
const testing = ref(false)
const testTarget = ref('')
const testResult = ref(null)
const saving = ref(false)
const saveMessage = ref('')
const saveOk = ref(true)

onMounted(() => {
  loadSettings()
})

async function loadSettings() {
  try {
    const res = await api.get('/api/settings/llm')
    if (res.success && res.data) {
      provider.value = res.data.provider || 'network'
      if (res.data.network) {
        network.api_key = res.data.network.api_key || ''
        network.base_url = res.data.network.base_url || ''
        network.model_name = res.data.network.model_name || ''
      }
      if (res.data.local) {
        local.api_key = res.data.local.api_key || ''
        local.base_url = res.data.local.base_url || ''
        local.model_name = res.data.local.model_name || ''
      }
    }
  } catch (e) {
    console.warn('Failed to load LLM settings:', e)
  }
}

async function testConnection(target) {
  testing.value = true
  testTarget.value = target
  testResult.value = null
  try {
    const res = await api.post('/api/settings/llm/test', { provider: target })
    testResult.value = res.data || { ok: false, message: 'No response' }
  } catch (e) {
    testResult.value = { ok: false, message: e.message || 'Test failed' }
  } finally {
    testing.value = false
  }
}

async function saveSettings() {
  saving.value = true
  saveMessage.value = ''
  try {
    const payload = {
      provider: provider.value,
      network: {
        api_key: network.api_key,
        base_url: network.base_url,
        model_name: network.model_name
      },
      local: {
        api_key: local.api_key,
        base_url: local.base_url,
        model_name: local.model_name
      }
    }
    const res = await api.put('/api/settings/llm', payload)
    if (res.success) {
      saveOk.value = true
      saveMessage.value = res.message || t('settings.saved')
    } else {
      saveOk.value = false
      saveMessage.value = res.error || 'Save failed'
    }
  } catch (e) {
    saveOk.value = false
    saveMessage.value = e.message || 'Save failed'
  } finally {
    saving.value = false
    if (saveOk.value) {
      setTimeout(() => { saveMessage.value = '' }, 2000)
    }
  }
}

function close() {
  emit('close')
}
</script>

<style scoped>
.settings-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
}

.settings-panel {
  background: #FFF;
  border-radius: 12px;
  width: 520px;
  max-height: 80vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2);
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid #EEE;
  background: #000;
  color: #FFF;
  border-radius: 12px 12px 0 0;
}

.panel-title {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 1rem;
  letter-spacing: 1px;
}

.close-btn {
  background: none;
  border: none;
  color: #FFF;
  font-size: 1.4rem;
  cursor: pointer;
  padding: 0;
  line-height: 1;
}

.panel-body {
  padding: 24px;
}

.section {
  margin-bottom: 24px;
}

.section-label {
  display: block;
  font-weight: 600;
  font-size: 0.85rem;
  margin-bottom: 10px;
  color: #333;
}

.section-header {
  font-weight: 600;
  font-size: 0.9rem;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #333;
}

.section-icon {
  font-size: 1.1rem;
}

.toggle-group {
  display: flex;
  background: #F5F5F5;
  border-radius: 8px;
  padding: 4px;
  gap: 4px;
}

.toggle-btn {
  flex: 1;
  border: none;
  background: transparent;
  padding: 10px 16px;
  font-size: 0.85rem;
  font-weight: 600;
  color: #666;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
}

.toggle-btn.active {
  background: #000;
  color: #FFF;
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}

.field {
  margin-bottom: 14px;
}

.field label {
  display: block;
  font-size: 0.78rem;
  color: #666;
  margin-bottom: 5px;
  font-weight: 500;
}

.field input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #DDD;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  outline: none;
  box-sizing: border-box;
}

.field input:focus {
  border-color: #FF4500;
}

.optional {
  color: #AAA;
  font-weight: 400;
  font-size: 0.7rem;
}

.test-btn {
  background: none;
  border: 1px solid #DDD;
  padding: 8px 16px;
  font-size: 0.8rem;
  color: #666;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s;
  margin-top: 4px;
}

.test-btn:hover:not(:disabled) {
  border-color: #FF4500;
  color: #FF4500;
}

.test-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.test-result {
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 0.78rem;
  font-family: 'JetBrains Mono', monospace;
}

.test-result.ok {
  background: #E8F5E9;
  color: #2E7D32;
}

.test-result.fail {
  background: #FFEBEE;
  color: #C62828;
}

.panel-footer {
  padding: 16px 24px;
  border-top: 1px solid #EEE;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 16px;
}

.save-msg {
  font-size: 0.8rem;
  font-family: 'JetBrains Mono', monospace;
}

.save-msg.ok { color: #2E7D32; }
.save-msg.fail { color: #C62828; }

.save-btn {
  background: #000;
  color: #FFF;
  border: none;
  padding: 10px 24px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
  font-size: 0.85rem;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
  letter-spacing: 1px;
}

.save-btn:hover:not(:disabled) {
  background: #FF4500;
}

.save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.spin {
  display: inline-block;
  animation: spin-anim 0.8s linear infinite;
}

@keyframes spin-anim {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
