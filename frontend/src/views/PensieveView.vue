<template>
  <div class="pensieve-page">
    <header class="page-header">
      <div class="brand" @click="router.push('/')">MACFISH</div>
      <ArtifactLinks variant="dark" class="header-links" />
      <div class="header-right">
        <LanguageSwitcher />
        <button class="ghost-btn compact" @click="router.push('/')">
          ← {{ $t('pensieve.backHome') }}
        </button>
      </div>
    </header>

    <main>
      <section class="hero">
        <div class="hero-copy">
          <span class="eyebrow">{{ $t('pensieve.eyebrow') }}</span>
          <h1>
            {{ $t('pensieve.heroTitle') }}<br />
            <em>{{ $t('pensieve.heroAccent') }}</em>
          </h1>
          <p class="hero-desc">{{ $t('pensieve.heroDesc') }}</p>

          <div class="stats-row">
            <div class="stat">
              <b>{{ stats.memories }}</b>
              <span>{{ $t('pensieve.statMemories') }}</span>
            </div>
            <div class="stat">
              <b>{{ stats.strands }}</b>
              <span>{{ $t('pensieve.statStrands') }}</span>
            </div>
            <div class="stat">
              <b>{{ stats.vials }}</b>
              <span>{{ $t('pensieve.statVials') }}</span>
            </div>
            <div class="stat">
              <b>{{ stats.projects }}</b>
              <span>{{ $t('pensieve.statProjects') }}</span>
            </div>
          </div>
        </div>

        <div class="hero-basin">
          <PensieveBasin :memories="memories" :selected="selectedMemory" />
        </div>
      </section>

      <section class="workspace">
        <aside class="control-panel">
          <div class="panel-card">
            <h3>{{ $t('pensieve.harvestTitle') }}</h3>
            <p class="panel-desc">{{ $t('pensieve.harvestDesc') }}</p>
            <p class="auto-hint">{{ $t('pensieve.autoHarvestHint') }}</p>

            <label class="field-label">{{ $t('pensieve.harvest') }}</label>
            <select v-model="harvestScope" class="field">
              <option value="all">{{ $t('pensieve.scopeAll') }}</option>
              <option value="project">{{ $t('pensieve.scopeProject') }}</option>
              <option value="simulation">{{ $t('pensieve.scopeSimulation') }}</option>
              <option value="report">{{ $t('pensieve.scopeReport') }}</option>
            </select>

            <select
              v-if="harvestScope !== 'all'"
              v-model="harvestTarget"
              class="field"
            >
              <option value="" disabled>{{ $t('pensieve.targetPlaceholder') }}</option>
              <option v-for="option in harvestTargets" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>

            <button class="primary-btn full" :disabled="harvesting" @click="runHarvest">
              {{ harvesting ? $t('pensieve.harvesting') : $t('pensieve.harvestButton') }}
            </button>

            <p v-if="notice" class="notice">{{ notice }}</p>
          </div>

          <div class="panel-card">
            <h3>{{ $t('pensieve.searchButton') }}</h3>
            <div class="search-row">
              <input
                v-model="search"
                class="field"
                :placeholder="$t('pensieve.searchPlaceholder')"
                @keyup.enter="runSearch"
              />
              <button class="ghost-btn" @click="runSearch">→</button>
            </div>

            <div class="kind-list">
              <button
                v-for="option in kindOptions"
                :key="option.value || 'all'"
                class="kind-chip"
                :class="{ active: kind === option.value }"
                @click="kind = option.value"
              >
                {{ option.label }}
              </button>
            </div>
          </div>
        </aside>

        <section class="memory-column">
          <div class="column-head">
            <h2>{{ $t('pensieve.memoryList') }}</h2>
            <span>{{ $t('pensieve.memoryCount', { count: totalMemories }) }}</span>
          </div>

          <div v-if="loading" class="empty">{{ $t('pensieve.loading') }}</div>
          <div v-else-if="error" class="empty error">
            {{ $t('pensieve.error') }}: {{ error }}
            <button class="link-btn" @click="refreshAll">{{ $t('pensieve.retry') }}</button>
          </div>
          <div v-else-if="!memories.length" class="empty">
            {{ search || kind ? $t('pensieve.emptyFiltered') : $t('pensieve.emptyMemories') }}
          </div>

          <div v-else class="memory-list">
            <article
              v-for="memory in memories"
              :key="memory.memory_id"
              class="memory-card"
              :class="[`kind-${memory.kind}`, { active: selectedMemory?.memory_id === memory.memory_id }]"
              @click="selectMemory(memory)"
            >
              <div class="memory-top">
                <span class="kind-badge">{{ kindLabel(memory.kind) }}</span>
                <label class="distill-check" @click.stop>
                  <input
                    type="checkbox"
                    :checked="isInDistill(memory.memory_id)"
                    @change="toggleDistill(memory.memory_id)"
                  />
                  <span>{{ $t('pensieve.addToDistill') }}</span>
                </label>
              </div>
              <h3>{{ memory.title }}</h3>
              <p>{{ memory.summary || memory.content }}</p>
              <div class="memory-bottom">
                <span>{{ sourceText(memory) }}</span>
                <span class="importance">
                  {{ $t('pensieve.importance') }} {{ memory.importance }}
                </span>
              </div>
            </article>
          </div>
        </section>

        <aside class="detail-panel">
          <div v-if="!selectedMemory" class="empty detail-empty">
            {{ $t('pensieve.detailEmpty') }}
          </div>

          <template v-else>
            <div class="detail-head">
              <span class="kind-badge">{{ kindLabel(selectedMemory.kind) }}</span>
              <h2>{{ selectedMemory.title }}</h2>
              <div class="detail-meta">
                <span>{{ sourceText(selectedMemory) }}</span>
                <span>{{ formatDate(selectedMemory.created_at) }}</span>
              </div>
            </div>

            <div class="detail-actions">
              <button class="primary-btn small" :disabled="replayLoading" @click="runReplay">
                {{ replayLoading ? $t('pensieve.replaying') : $t('pensieve.replay') }}
              </button>
              <button
                v-if="canOpenSource(selectedMemory)"
                class="ghost-btn small"
                @click="openSource(selectedMemory)"
              >
                {{ $t('pensieve.openSource') }} ↗
              </button>
              <button class="ghost-btn small danger" @click="removeMemory(selectedMemory)">
                {{ $t('pensieve.deleteMemory') }}
              </button>
            </div>

            <div class="detail-content">{{ selectedMemory.content }}</div>

            <div v-if="selectedMemory.tags?.length" class="chip-group">
              <span class="chip-label">{{ $t('pensieve.tags') }}</span>
              <span v-for="tag in selectedMemory.tags" :key="tag" class="chip">{{ tag }}</span>
            </div>

            <div v-if="selectedMemory.entities?.length" class="chip-group">
              <span class="chip-label">{{ $t('pensieve.entities') }}</span>
              <span v-for="entity in selectedMemory.entities" :key="entity" class="chip entity">{{ entity }}</span>
            </div>

            <div class="related">
              <h3>{{ $t('pensieve.relatedStrands') }}</h3>
              <div v-if="!selected.strands.length" class="muted">
                {{ $t('pensieve.noRelated') }}
              </div>
              <button
                v-for="strand in selected.strands"
                :key="strand.strand_id"
                class="related-item"
                @click="selectRelated(strand.memory.memory_id)"
              >
                <span class="relation">{{ strand.relation }}</span>
                <span class="related-title">{{ strand.memory.title }}</span>
              </button>
            </div>
          </template>
        </aside>
      </section>

      <section class="distill-dock">
        <div class="distill-head">
          <div>
            <h2>{{ $t('pensieve.distillTitle') }}</h2>
            <p>{{ $t('pensieve.distillDesc') }}</p>
          </div>
          <span class="selection-count">
            {{ $t('pensieve.distillSelected', { count: selectedForDistill.length }) }}
          </span>
        </div>

        <div class="selected-chips">
          <button
            v-for="memoryId in selectedForDistill"
            :key="memoryId"
            class="chip removable"
            @click="toggleDistill(memoryId)"
          >
            {{ memoryTitle(memoryId) }} ×
          </button>
          <span v-if="!selectedForDistill.length" class="muted">
            {{ $t('pensieve.distillEmpty') }}
          </span>
        </div>

        <div class="distill-row">
          <input
            v-model="distillQuestion"
            class="field"
            :placeholder="$t('pensieve.distillPlaceholder')"
            @keyup.enter="runDistill"
          />
          <button class="primary-btn" :disabled="distilling" @click="runDistill">
            {{ distilling ? $t('pensieve.distilling') : $t('pensieve.distillButton') }}
          </button>
        </div>

        <div v-if="vialResult" class="vial-result">
          <div class="vial-art" aria-hidden="true"><span></span></div>
          <div class="vial-result-body">
            <h3>{{ vialResult.title }}</h3>
            <p>{{ vialResult.content }}</p>
            <span class="badge">
              {{ vialResult.used_llm ? $t('pensieve.vialUsedLlm') : $t('pensieve.vialFallback') }}
            </span>
          </div>
        </div>
      </section>

      <section class="vials-section">
        <div class="section-head">
          <h2>{{ $t('pensieve.vialsTitle') }}</h2>
          <span>{{ vials.length }}</span>
        </div>
        <div v-if="!vials.length" class="empty">{{ $t('pensieve.vialsEmpty') }}</div>
        <div v-else class="vial-grid">
          <article v-for="vial in vials" :key="vial.vial_id" class="vial-card">
            <div class="vial-art small" aria-hidden="true"><span></span></div>
            <div class="vial-card-body">
              <h3>{{ vial.title }}</h3>
              <p>{{ vial.content }}</p>
              <div class="vial-foot">
                <span class="badge">
                  {{ vial.used_llm ? $t('pensieve.vialUsedLlm') : $t('pensieve.vialFallback') }}
                </span>
                <button class="link-btn danger" @click="removeVial(vial)">
                  {{ $t('pensieve.deleteVial') }}
                </button>
              </div>
            </div>
          </article>
        </div>
      </section>
    </main>

    <div v-if="replay" class="overlay" @click.self="replay = null">
      <div class="replay-card">
        <button class="close-btn" @click="replay = null">×</button>
        <span class="eyebrow">{{ $t('pensieve.replayTitle') }}</span>
        <h2>{{ replay.memory.title }}</h2>

        <div v-if="replay.citations?.length" class="replay-citations">
          <span class="citation-label">
            {{ $t('pensieve.citationsTitle') }} · {{ replay.citation_count }}
          </span>
          <button
            v-for="citation in replay.citations"
            :key="citation.memory_id"
            class="citation-chip"
            :title="citation.detail"
            @click="openCitation(citation)"
          >
            {{ citation.label }} ↗
          </button>
        </div>

        <div class="replay-narration">{{ replay.narration }}</div>

        <div class="replay-grid">
          <div>
            <h4>{{ $t('pensieve.replaySetting') }}</h4>
            <p>{{ replay.setting }}</p>
          </div>
          <div>
            <h4>{{ $t('pensieve.replayCast') }}</h4>
            <div class="chips">
              <span v-for="member in replay.cast" :key="member" class="chip entity">{{ member }}</span>
              <span v-if="!replay.cast.length" class="muted">—</span>
            </div>
          </div>
        </div>

        <h4>{{ $t('pensieve.replayTimeline') }}</h4>
        <ol class="timeline">
          <li v-for="item in replay.timeline" :key="item.memory_id">
            <span class="timeline-dot"></span>
            <div class="timeline-body">
              <b>{{ item.title }}</b>
              <p>{{ item.content }}</p>
              <time>{{ formatDate(item.occurred_at) }}</time>
              <button
                v-if="item.citation"
                class="timeline-citation"
                :title="item.citation.detail"
                @click="openCitation(item.citation)"
              >
                ↳ {{ item.citation.label }}
              </button>
            </div>
          </li>
        </ol>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import ArtifactLinks from '../components/ArtifactLinks.vue'
import PensieveBasin from '../components/PensieveBasin.vue'
import {
  deleteMemory,
  deleteVial,
  distillMemories,
  getMemories,
  getMemoryDetail,
  getPensieveOverview,
  getVials,
  harvestMemories,
  replayMemory
} from '../api/pensieve'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const overview = ref(null)
const loading = ref(true)
const error = ref('')
const memories = ref([])
const totalMemories = ref(0)
const selected = ref(null)
const search = ref('')
const kind = ref('')
const harvestScope = ref('all')
const harvestTarget = ref('')
const harvesting = ref(false)
const notice = ref('')
const replay = ref(null)
const replayLoading = ref(false)
const selectedForDistill = ref([])
const distillQuestion = ref('')
const distilling = ref(false)
const vialResult = ref(null)
const vials = ref([])

const stats = computed(() => overview.value?.stats || {
  memories: 0, strands: 0, vials: 0, projects: 0,
  simulations: 0, reports: 0
})
const selectedMemory = computed(() => selected.value?.memory || null)

const kindOptions = computed(() => [
  { value: '', label: t('pensieve.kindAll') },
  ...['seed', 'world', 'entity', 'agent', 'episode', 'voice', 'prophecy', 'insight']
    .map(value => ({ value, label: t(`pensieve.kind_${value}`) }))
])

const harvestTargets = computed(() => {
  if (!overview.value) return []
  if (harvestScope.value === 'project') {
    return (overview.value.projects || []).map(item => ({
      value: item.project_id,
      label: item.name || item.project_id
    }))
  }
  if (harvestScope.value === 'simulation') {
    return (overview.value.simulations || []).map(item => ({
      value: item.simulation_id,
      label: item.simulation_id
    }))
  }
  if (harvestScope.value === 'report') {
    return (overview.value.reports || []).map(item => ({
      value: item.report_id,
      label: item.title || item.report_id
    }))
  }
  return []
})

const memoryById = computed(() =>
  Object.fromEntries(memories.value.map(memory => [memory.memory_id, memory])))

function kindLabel(value) {
  return t(`pensieve.kind_${value}`)
}

function memoryTitle(memoryId) {
  return memoryById.value[memoryId]?.title || memoryId
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 26)
  return date.toLocaleString()
}

function sourceText(memory) {
  if (memory.report_id) return `${t('pensieve.sourceReport')} · ${memory.report_id}`
  if (memory.simulation_id) return `${t('pensieve.sourceSimulation')} · ${memory.simulation_id}`
  if (memory.project_id) return `${t('pensieve.sourceProject')} · ${memory.project_id}`
  return memory.source_type || '—'
}

function canOpenSource(memory) {
  return Boolean(memory.report_id || memory.simulation_id || memory.project_id)
}

function openSource(memory) {
  if (memory.report_id) router.push(`/report/${memory.report_id}`)
  else if (memory.simulation_id) router.push(`/simulation/${memory.simulation_id}`)
  else if (memory.project_id) router.push(`/process/${memory.project_id}`)
}

// 回放里的引用直接跳回原始产物；关闭弹窗，避免返回时还压着一层
function openCitation(citation) {
  if (!citation?.route) return
  replay.value = null
  router.push(citation.route)
}

async function loadOverview() {
  const response = await getPensieveOverview()
  overview.value = response.data
  if (harvestScope.value !== 'all' && !harvestTarget.value) {
    harvestTarget.value = harvestTargets.value[0]?.value || ''
  }
}

async function loadMemories() {
  const params = { limit: 150 }
  if (search.value.trim()) params.q = search.value.trim()
  if (kind.value) params.kind = kind.value
  const response = await getMemories(params)
  memories.value = response.data
  totalMemories.value = response.total || response.count || response.data.length
}

async function loadVials() {
  const response = await getVials(50)
  vials.value = response.data
}

async function refreshAll() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([loadOverview(), loadMemories(), loadVials()])
    const requested = route.query.memory
    if (requested) {
      const target = memories.value.find(memory => memory.memory_id === requested)
      await selectMemory(target || { memory_id: requested })
    } else if (!selected.value && memories.value.length) {
      // 第一次进盆时先自动沉入一条记忆，避免右侧永远是空的
      await selectMemory(memories.value[0])
    }
    if (route.query.replay === '1' && selectedMemory.value) {
      await runReplay()
    }
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function runSearch() {
  loadMemories().catch(err => { error.value = err.message })
}

async function runHarvest() {
  if (harvestScope.value !== 'all' && !harvestTarget.value) {
    notice.value = t('pensieve.targetPlaceholder')
    return
  }
  harvesting.value = true
  notice.value = ''
  try {
    const response = await harvestMemories({
      scope: harvestScope.value,
      target_id: harvestScope.value === 'all' ? '' : harvestTarget.value
    })
    notice.value = t('pensieve.harvestDone', {
      created: response.data.created,
      updated: response.data.updated
    })
    await Promise.all([loadOverview(), loadMemories(), loadVials()])
  } catch (err) {
    notice.value = `${t('pensieve.harvestError')}: ${err.message}`
  } finally {
    harvesting.value = false
  }
}

async function selectMemory(memory) {
  try {
    const response = await getMemoryDetail(memory.memory_id)
    selected.value = response.data
    replay.value = null
  } catch (err) {
    notice.value = err.message
  }
}

function selectRelated(memoryId) {
  selectMemory({ memory_id: memoryId })
}

function isInDistill(memoryId) {
  return selectedForDistill.value.includes(memoryId)
}

function toggleDistill(memoryId) {
  const index = selectedForDistill.value.indexOf(memoryId)
  if (index >= 0) selectedForDistill.value.splice(index, 1)
  else selectedForDistill.value.push(memoryId)
}

async function runReplay() {
  if (!selectedMemory.value) return
  replayLoading.value = true
  try {
    const response = await replayMemory(selectedMemory.value.memory_id)
    replay.value = response.data
  } catch (err) {
    notice.value = err.message
  } finally {
    replayLoading.value = false
  }
}

async function runDistill() {
  if (!selectedForDistill.value.length) {
    notice.value = t('pensieve.distillEmpty')
    return
  }
  distilling.value = true
  notice.value = ''
  try {
    const response = await distillMemories(
      selectedForDistill.value,
      distillQuestion.value
    )
    vialResult.value = response.data
    selectedForDistill.value = []
    distillQuestion.value = ''
    await Promise.all([loadOverview(), loadMemories(), loadVials()])
  } catch (err) {
    notice.value = err.message
  } finally {
    distilling.value = false
  }
}

async function removeVial(vial) {
  if (!window.confirm(`${t('pensieve.deleteVial')} · ${vial.title}?`)) return
  try {
    await deleteVial(vial.vial_id)
    if (vialResult.value?.vial_id === vial.vial_id) vialResult.value = null
    await Promise.all([loadOverview(), loadVials()])
  } catch (err) {
    notice.value = err.message
  }
}

async function removeMemory(memory) {
  if (!window.confirm(t('pensieve.confirmDeleteMemory'))) return
  try {
    await deleteMemory(memory.memory_id)
    selectedForDistill.value = selectedForDistill.value.filter(id => id !== memory.memory_id)
    if (selectedMemory.value?.memory_id === memory.memory_id) selected.value = null
    await Promise.all([loadOverview(), loadMemories()])
  } catch (err) {
    notice.value = err.message
  }
}

watch(kind, () => runSearch())
watch(harvestScope, () => {
  harvestTarget.value = harvestTargets.value[0]?.value || ''
})

onMounted(refreshAll)
</script>

<style scoped>
.pensieve-page {
  --ink: #000000;
  --paper: #ffffff;
  --orange: #FF4500;
  --gray-text: #666666;
  --gray-light: #F5F5F5;
  --surface: #FAFAFA;
  --border: #E5E5E5;
  --border-strong: #CCCCCC;
  --muted: #999999;
  min-height: 100vh;
  background: var(--paper);
  color: var(--ink);
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  padding-bottom: 40px;
  overflow-x: hidden;
}

.hero-copy,
.hero-basin,
.workspace,
.control-panel,
.memory-column,
.detail-panel,
.distill-dock,
.vials-section {
  min-width: 0;
}

/* 顶部导航：沿用首页的黑色 navbar */
.page-header {
  position: sticky;
  top: 0;
  z-index: 50;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 0 40px;
  border-bottom: 1px solid #000;
  background: var(--ink);
  color: #fff;
}

.brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  letter-spacing: 1.2px;
  color: #fff;
  cursor: pointer;
  flex: none;
}

.header-links {
  flex: 1;
  justify-content: center;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: none;
}

.header-right :deep(.switcher-trigger) {
  color: rgba(255, 255, 255, 0.75);
  border-color: rgba(255, 255, 255, 0.24);
  background: transparent;
}

.header-right :deep(.switcher-trigger:hover) {
  color: #fff;
  border-color: rgba(255, 255, 255, 0.6);
}

.header-right :deep(.switcher-dropdown) {
  background: #fff;
  border-color: var(--border);
}

.header-right :deep(.switcher-option) {
  color: #333;
}

.header-right :deep(.switcher-option:hover) {
  background: var(--gray-light);
}

/* Hero */
.hero {
  max-width: 1320px;
  margin: 0 auto;
  padding: 64px 40px 36px;
  display: grid;
  grid-template-columns: minmax(0, 1.06fr) minmax(320px, 0.94fr);
  gap: 40px;
  align-items: center;
  border-bottom: 1px solid var(--border);
}

.eyebrow {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--orange);
  border-left: 3px solid var(--orange);
  padding-left: 10px;
  text-transform: uppercase;
  margin-bottom: 18px;
}

.hero h1 {
  font-size: clamp(2.6rem, 4.6vw, 4.2rem);
  line-height: 1.08;
  letter-spacing: -2px;
  font-weight: 500;
  margin-bottom: 22px;
  color: var(--ink);
}

.hero h1 em {
  font-style: normal;
  color: var(--orange);
}

.hero-desc {
  max-width: 560px;
  color: var(--gray-text);
  font-size: 1rem;
  line-height: 1.8;
}

.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  margin-top: 34px;
  padding-top: 22px;
  border-top: 1px solid var(--border);
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-right: 34px;
  margin-right: 0;
  border-right: 1px solid var(--border);
}

.stat + .stat { padding-left: 34px; }
.stat:last-child { border-right: none; padding-right: 0; }

.stat b {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.9rem;
  line-height: 1;
  font-weight: 700;
  color: var(--ink);
}

.stat span {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.hero-basin {
  display: flex;
  justify-content: center;
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 12px 16px 6px;
}

/* 工作区 */
.workspace {
  max-width: 1320px;
  margin: 0 auto;
  padding: 34px 40px 10px;
  display: grid;
  grid-template-columns: 252px minmax(0, 1fr) 380px;
  gap: 20px;
  align-items: start;
}

.control-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  position: sticky;
  top: 80px;
}

.panel-card {
  background: #fff;
  border: 1px solid var(--border);
  padding: 18px;
}

.panel-card h3 {
  font-size: 0.9rem;
  font-weight: 700;
  margin-bottom: 8px;
  letter-spacing: 0.02em;
  color: var(--ink);
}

.panel-desc {
  color: var(--gray-text);
  font-size: 0.76rem;
  line-height: 1.6;
  margin-bottom: 14px;
}

.auto-hint {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.64rem;
  line-height: 1.6;
  color: var(--muted);
  border-left: 2px solid var(--border);
  padding-left: 8px;
  margin: -4px 0 14px;
}

.field-label {
  display: block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--muted);
  margin-bottom: 6px;
  text-transform: uppercase;
}

.field {
  width: 100%;
  background: #fff;
  border: 1px solid var(--border-strong);
  color: var(--ink);
  padding: 9px 11px;
  font-family: inherit;
  font-size: 0.82rem;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.field:focus {
  border-color: var(--ink);
  box-shadow: 0 0 0 1px var(--ink);
}

select.field {
  appearance: none;
  margin-bottom: 10px;
  cursor: pointer;
  max-width: 100%;
  min-width: 0;
}

.search-row {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}

.search-row .field { flex: 1; min-width: 0; }

.kind-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 14px;
}

.kind-chip {
  background: #fff;
  border: 1px solid var(--border);
  color: var(--gray-text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.65rem;
  padding: 5px 8px;
  cursor: pointer;
  transition: all 0.18s;
}

.kind-chip:hover {
  color: var(--ink);
  border-color: var(--ink);
}

.kind-chip.active {
  color: #fff;
  background: var(--ink);
  border-color: var(--ink);
  font-weight: 700;
}

.primary-btn {
  background: var(--ink);
  color: #fff;
  border: 1px solid var(--ink);
  padding: 10px 18px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 0.76rem;
  letter-spacing: 0.03em;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s, transform 0.2s;
}

.primary-btn:hover:not(:disabled) {
  background: var(--orange);
  border-color: var(--orange);
  transform: translateY(-1px);
}

.primary-btn:disabled { opacity: 0.45; cursor: wait; }
.primary-btn.full { width: 100%; margin-top: 2px; }
.primary-btn.small { padding: 8px 13px; font-size: 0.7rem; }

.ghost-btn {
  background: #fff;
  border: 1px solid var(--border-strong);
  color: #333;
  padding: 9px 12px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.ghost-btn:hover {
  border-color: var(--ink);
  color: var(--ink);
}

.ghost-btn.compact { padding: 6px 10px; font-size: 0.68rem; }
.ghost-btn.small { padding: 7px 11px; font-size: 0.68rem; }
.ghost-btn.danger:hover { border-color: #B23A2F; color: #B23A2F; }

.page-header .ghost-btn {
  background: transparent;
  border-color: rgba(255, 255, 255, 0.3);
  color: rgba(255, 255, 255, 0.8);
}

.page-header .ghost-btn:hover {
  border-color: #fff;
  color: #fff;
}

.notice {
  margin-top: 12px;
  font-size: 0.72rem;
  line-height: 1.55;
  color: var(--orange);
  border-left: 2px solid var(--orange);
  padding-left: 9px;
}

.column-head,
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}

.column-head h2,
.section-head h2 {
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--ink);
}

.column-head span,
.section-head span {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--muted);
}

.memory-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
  max-height: 780px;
  overflow: auto;
  padding-right: 4px;
}

.memory-card {
  --kind-color: #FF4500;
  background: #fff;
  border: 1px solid var(--border);
  border-left: 2px solid var(--kind-color);
  padding: 14px 15px;
  cursor: pointer;
  transition: border-color 0.2s, transform 0.2s, background 0.2s;
}

.memory-card:hover {
  border-color: var(--ink);
  transform: translateX(2px);
}

.memory-card.active {
  border-color: var(--ink);
  background: var(--surface);
}

.memory-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.kind-badge {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  letter-spacing: 0.06em;
  color: var(--kind-color, var(--orange));
  border: 1px solid color-mix(in srgb, var(--kind-color, var(--orange)) 45%, transparent);
  padding: 2px 7px;
}

.distill-check {
  display: flex;
  align-items: center;
  gap: 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.58rem;
  color: var(--muted);
  cursor: pointer;
  user-select: none;
}

.distill-check input { accent-color: var(--orange); cursor: pointer; }

.memory-card h3 {
  font-size: 0.92rem;
  line-height: 1.4;
  margin-bottom: 6px;
  color: var(--ink);
  font-weight: 700;
}

.memory-card p {
  color: var(--gray-text);
  font-size: 0.78rem;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.memory-bottom {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-top: 10px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.61rem;
  color: var(--muted);
}

.importance { color: var(--gray-text); white-space: nowrap; }

/* 记忆类型色：沿用首页的橙/黑/灰基调，辅以工作台既有的蓝绿紫 */
.kind-seed { --kind-color: #FF4500; }
.kind-world { --kind-color: #000000; }
.kind-entity { --kind-color: #3B82F6; }
.kind-agent { --kind-color: #8B5CF6; }
.kind-episode { --kind-color: #10B981; }
.kind-voice { --kind-color: #B23A2F; }
.kind-prophecy { --kind-color: #F59E0B; }
.kind-insight { --kind-color: #FF4500; }

.detail-panel {
  position: sticky;
  top: 80px;
  background: #fff;
  border: 1px solid var(--border);
  padding: 20px;
  max-height: calc(100vh - 100px);
  overflow: auto;
}

.detail-empty { min-height: 220px; display: flex; align-items: center; justify-content: center; }

.detail-head h2 {
  font-size: 1.15rem;
  line-height: 1.4;
  margin: 10px 0 8px;
  font-weight: 700;
  color: var(--ink);
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.62rem;
  color: var(--muted);
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin: 16px 0;
}

.detail-content {
  margin: 14px 0 18px;
  padding: 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: #333;
  font-size: 0.82rem;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 340px;
  overflow: auto;
}

.chip-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}

.chip-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  letter-spacing: 0.1em;
  color: var(--muted);
  margin-right: 2px;
  text-transform: uppercase;
}

.chip {
  display: inline-block;
  border: 1px solid var(--border);
  color: var(--gray-text);
  font-size: 0.66rem;
  padding: 3px 7px;
  line-height: 1.3;
  background: #fff;
}

.chip.entity {
  color: var(--ink);
  border-color: var(--border-strong);
}

.chip.removable {
  cursor: pointer;
  background: var(--surface);
  transition: border-color 0.2s, color 0.2s;
}

.chip.removable:hover { border-color: #B23A2F; color: #B23A2F; }

.related { margin-top: 20px; }

.related h3,
.replay-card h4 {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 9px;
}

.related-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  background: transparent;
  border: none;
  border-bottom: 1px dashed var(--border);
  color: #333;
  text-align: left;
  padding: 8px 0;
  cursor: pointer;
  font-size: 0.76rem;
}

.related-item:hover { color: var(--orange); }
.related-item .relation {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.56rem;
  color: var(--muted);
  flex: none;
}

.related-title { line-height: 1.4; }

.muted { color: var(--muted); font-size: 0.76rem; }

.empty {
  border: 1px dashed var(--border-strong);
  padding: 30px 18px;
  text-align: center;
  color: var(--muted);
  font-size: 0.8rem;
  line-height: 1.6;
  background: var(--surface);
}

.empty.error { color: #B23A2F; border-color: #E0B9B4; background: #FDF3F2; }

.link-btn {
  background: none;
  border: none;
  color: var(--ink);
  font-family: inherit;
  font-size: inherit;
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.link-btn:hover { color: var(--orange); }
.link-btn.danger { color: #B23A2F; margin-left: auto; }

.distill-dock {
  max-width: 1320px;
  margin: 30px auto 0;
  padding: 22px 40px;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.distill-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 14px;
}

.distill-head h2 { font-size: 1.02rem; font-weight: 700; margin-bottom: 5px; }
.distill-head p { color: var(--gray-text); font-size: 0.78rem; }

.selection-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--orange);
  white-space: nowrap;
}

.selected-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  min-height: 28px;
  margin-bottom: 13px;
}

.distill-row {
  display: flex;
  gap: 10px;
}

.distill-row .field { flex: 1; }

.vial-result {
  display: flex;
  gap: 18px;
  margin-top: 20px;
  padding: 18px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--orange);
  background: #fff;
}

.vial-result-body h3 { font-size: 1rem; font-weight: 700; margin-bottom: 8px; color: var(--ink); }
.vial-result-body p {
  color: #333;
  font-size: 0.82rem;
  line-height: 1.7;
  white-space: pre-wrap;
  max-height: 260px;
  overflow: auto;
  margin-bottom: 10px;
}

.badge {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.58rem;
  letter-spacing: 0.08em;
  color: var(--gray-text);
  border: 1px solid var(--border);
  padding: 2px 7px;
  background: var(--surface);
}

/* 记忆瓶：透明玻璃 + 橙色记忆液 */
.vial-art {
  position: relative;
  width: 54px;
  height: 94px;
  flex: none;
  border: 1px solid var(--border-strong);
  border-radius: 4px 4px 16px 16px;
  background: linear-gradient(180deg, #FFFFFF, #F2F2F2);
  overflow: hidden;
}

.vial-art.small { width: 38px; height: 66px; }

.vial-art::before {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  width: 60%;
  height: 9px;
  transform: translateX(-50%);
  border: 1px solid var(--border-strong);
  border-top: none;
  border-radius: 0 0 4px 4px;
}

.vial-art span {
  position: absolute;
  left: 8%;
  right: 8%;
  bottom: 8%;
  height: 58%;
  border-radius: 2px 2px 12px 12px;
  background:
    radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.85), transparent 42%),
    linear-gradient(180deg, #FF7A3D, #FF4500);
  box-shadow: none;
}

.vials-section {
  max-width: 1320px;
  margin: 0 auto;
  padding: 34px 40px 80px;
}

.vial-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 12px;
}

.vial-card {
  display: flex;
  gap: 13px;
  padding: 16px;
  background: #fff;
  border: 1px solid var(--border);
  transition: border-color 0.2s;
}

.vial-card:hover { border-color: var(--ink); }

.vial-card-body { min-width: 0; }
.vial-card-body h3 { font-size: 0.88rem; font-weight: 700; margin-bottom: 7px; line-height: 1.4; color: var(--ink); }
.vial-card-body p {
  color: var(--gray-text);
  font-size: 0.75rem;
  line-height: 1.6;
  max-height: 140px;
  overflow: auto;
  white-space: pre-wrap;
  margin-bottom: 10px;
}

.vial-foot { display: flex; align-items: center; gap: 10px; }

.overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 26px;
}

.replay-card {
  position: relative;
  width: min(860px, 100%);
  max-height: 88vh;
  overflow: auto;
  background: #fff;
  border: 1px solid #000;
  padding: 32px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.28);
}

.replay-card h2 { font-size: 1.4rem; font-weight: 700; margin: 4px 0 18px; line-height: 1.3; color: var(--ink); }

.replay-citations {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}

.citation-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
  margin-right: 2px;
}

.citation-chip {
  max-width: 240px;
  padding: 5px 8px;
  background: #fff;
  border: 1px solid var(--border);
  border-left: 2px solid var(--orange);
  color: #333;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.66rem;
  line-height: 1.35;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: border-color 0.2s, color 0.2s;
}

.citation-chip:hover { border-color: var(--ink); color: var(--orange); }

.close-btn {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 32px;
  height: 32px;
  background: #fff;
  border: 1px solid var(--border-strong);
  color: #333;
  font-size: 1.2rem;
  cursor: pointer;
  line-height: 1;
}

.close-btn:hover { color: var(--orange); border-color: var(--ink); }

.replay-narration {
  padding: 18px 20px;
  margin-bottom: 22px;
  border-left: 3px solid var(--orange);
  background: var(--surface);
  color: #333;
  font-family: 'Noto Serif SC', Georgia, serif;
  font-size: 0.92rem;
  line-height: 1.9;
  white-space: pre-wrap;
}

.replay-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr);
  gap: 18px;
  margin-bottom: 22px;
}

.replay-grid p { color: var(--gray-text); font-size: 0.82rem; }

.chips { display: flex; flex-wrap: wrap; gap: 5px; }

.timeline {
  list-style: none;
  position: relative;
  padding-left: 18px;
  border-left: 1px solid var(--border);
}

.timeline li {
  position: relative;
  padding: 0 0 16px 10px;
}

.timeline-dot {
  position: absolute;
  left: -23px;
  top: 5px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--orange);
}

.timeline-body b { font-size: 0.82rem; color: var(--ink); }
.timeline-body p { color: var(--gray-text); font-size: 0.76rem; line-height: 1.6; margin: 4px 0; }
.timeline-body time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  color: var(--muted);
}

.timeline-citation {
  display: inline-block;
  margin-top: 5px;
  padding: 0;
  background: none;
  border: none;
  color: var(--muted);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  line-height: 1.5;
  text-align: left;
  cursor: pointer;
}

.timeline-citation:hover {
  color: var(--orange);
  text-decoration: underline;
  text-underline-offset: 2px;
}

@media (max-width: 1180px) {
  .workspace { grid-template-columns: 240px minmax(0, 1fr); }
  .detail-panel { grid-column: 1 / -1; position: static; max-height: none; }
}

@media (max-width: 880px) {
  .hero { grid-template-columns: minmax(0, 1fr); padding-top: 36px; }
  .hero-basin { order: -1; }
  .workspace { grid-template-columns: 1fr; }
  .control-panel { position: static; }
  .memory-list { max-height: none; }
  .replay-grid { grid-template-columns: 1fr; }
  .page-header { padding: 0 16px; }
  .hero, .workspace, .vials-section { padding-left: 16px; padding-right: 16px; }
  .distill-dock { padding-left: 16px; padding-right: 16px; }
  .header-right .ghost-btn.compact { display: none; }
}
</style>
