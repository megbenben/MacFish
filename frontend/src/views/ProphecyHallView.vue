<template>
  <div class="prophecy-page">
    <header class="page-header">
      <div class="brand" @click="router.push('/')">MACFISH</div>
      <ArtifactLinks variant="dark" class="header-links" />
      <div class="header-right">
        <LanguageSwitcher />
        <button class="ghost-btn compact" @click="router.push('/')">
          ← {{ $t('prophecy.backHome') }}
        </button>
      </div>
    </header>

    <main>
      <section class="hero">
        <div class="hero-copy">
          <span class="eyebrow">{{ $t('prophecy.eyebrow') }}</span>
          <h1>
            {{ $t('prophecy.heroTitle') }}<br />
            <em>{{ $t('prophecy.heroAccent') }}</em>
          </h1>
          <p class="hero-desc">{{ $t('prophecy.heroDesc') }}</p>
        </div>

        <div class="hero-visual">
          <div class="constellation" aria-hidden="true"></div>
          <ProphecyOrb
            v-if="orbs.length"
            :orb="orbs[0]"
            size="xl"
            :show-meta="false"
            @open="openDetail"
          />
          <div v-else class="empty-orb" aria-hidden="true"><span>✦</span></div>
        </div>
      </section>

      <section
        v-if="reminders.count"
        class="reminder-bar"
        :class="{ overdue: reminders.overdue > 0 }"
      >
        <div class="reminder-copy">
          <span class="reminder-kicker">{{ $t('prophecy.remindersTitle') }}</span>
          <p>{{ reminderSummary }}</p>
        </div>
        <div class="reminder-items">
          <button
            v-for="item in reminders.items.slice(0, 4)"
            :key="item.orb_id"
            class="reminder-item"
            :class="item.deadline_state"
            @click="openDetail(item)"
          >
            <span class="reminder-title">{{ item.title }}</span>
            <span class="reminder-days">{{ reminderDays(item) }}</span>
          </button>
          <span v-if="reminders.items.length > 4" class="reminder-more">
            +{{ reminders.items.length - 4 }}
          </span>
        </div>
      </section>

      <section class="stat-bar">
        <div class="stat-cell">
          <b>{{ stats.total }}</b><span>{{ $t('prophecy.statTotal') }}</span>
        </div>
        <div class="stat-cell">
          <b>{{ stats.sealed }}</b><span>{{ $t('prophecy.statSealed') }}</span>
        </div>
        <div class="stat-cell">
          <b>{{ stats.opened }}</b><span>{{ $t('prophecy.statOpened') }}</span>
        </div>
        <div class="stat-cell">
          <b>{{ stats.fulfilled }}</b><span>{{ $t('prophecy.statFulfilled') }}</span>
        </div>
        <div class="stat-cell">
          <b>{{ stats.partial }}</b><span>{{ $t('prophecy.statPartial') }}</span>
        </div>
        <div class="stat-cell">
          <b>{{ stats.shattered }}</b><span>{{ $t('prophecy.statShattered') }}</span>
        </div>
        <div class="stat-cell">
          <b>{{ stats.average_accuracy ?? '—' }}<i v-if="stats.average_accuracy != null">%</i></b>
          <span>{{ $t('prophecy.statAccuracy') }}</span>
        </div>
        <div class="stat-cell" v-if="calibration.n">
          <b>{{ calibration.brier_score }}</b>
          <span>{{ $t('prophecy.statBrier', { n: calibration.n }) }}</span>
        </div>
      </section>

      <section class="forge-panel">
        <div class="forge-copy">
          <h2>{{ $t('prophecy.forgeTitle') }}</h2>
          <p>{{ $t('prophecy.forgeDesc') }}</p>
        </div>

        <div v-if="availableReports.length" class="forge-controls">
          <select v-model="forgeReportId" class="field">
            <option v-for="report in availableReports" :key="report.report_id" :value="report.report_id">
              {{ report.title }}
            </option>
          </select>
          <button class="primary-btn" :disabled="forging" @click="runForge">
            {{ forging ? $t('prophecy.forging') : $t('prophecy.forgeButton') }}
          </button>
        </div>
        <div v-else class="forge-empty">{{ $t('prophecy.noReports') }}</div>

        <p v-if="notice" class="notice">{{ notice }}</p>
      </section>

      <section class="hall">
        <div class="hall-head">
          <div>
            <h2>{{ $t('prophecy.hallTitle') }}</h2>
            <p>{{ $t('prophecy.memoryBackflow') }}</p>
          </div>
          <div class="status-filters">
            <button
              v-for="filter in statusFilters"
              :key="filter.value || 'all'"
              class="filter-chip"
              :class="{ active: filterStatus === filter.value }"
              @click="filterStatus = filter.value"
            >
              {{ filter.label }}
            </button>
          </div>
        </div>

        <div v-if="loading" class="empty">{{ $t('prophecy.loading') }}</div>
        <div v-else-if="error" class="empty error">
          {{ $t('prophecy.error') }}: {{ error }}
          <button class="link-btn" @click="loadAll">{{ $t('prophecy.retry') }}</button>
        </div>
        <div v-else-if="!filteredOrbs.length" class="empty">{{ $t('prophecy.hallEmpty') }}</div>
        <div v-else class="orb-grid">
          <div v-for="orb in filteredOrbs" :key="orb.orb_id" class="orb-cell">
            <ProphecyOrb :orb="orb" size="md" @open="openDetail" />
          </div>
        </div>
      </section>
    </main>

    <div v-if="detailOpen" class="overlay" @click.self="closeDetail">
      <div class="detail-card">
        <button class="close-btn" @click="closeDetail">×</button>

        <div v-if="detailLoading" class="empty detail-loading">{{ $t('prophecy.loading') }}</div>

        <div v-else-if="selected" class="detail-layout">
          <aside class="detail-left">
            <ProphecyOrb :orb="selected" size="xl" :show-meta="false" :interactive="false" />

            <div class="confidence-ring">
              <svg viewBox="0 0 120 120">
                <circle cx="60" cy="60" r="52" class="ring-track" />
                <circle
                  cx="60"
                  cy="60"
                  r="52"
                  class="ring-value"
                  :class="selected.status"
                  :style="{ strokeDasharray: ringDash }"
                  transform="rotate(-90 60 60)"
                />
              </svg>
              <div class="ring-label">
                <b>{{ selected.confidence }}%</b>
                <span>{{ $t('prophecy.confidence') }}</span>
              </div>
            </div>

            <div class="meta-list">
              <div>
                <span>{{ $t('prophecy.seer') }}</span>
                <b>{{ selected.seer || '—' }}</b>
              </div>
              <div>
                <span>{{ $t('prophecy.deadline') }}</span>
                <b>{{ selected.deadline || '—' }}</b>
              </div>
              <div>
                <span>{{ $t('prophecy.horizon', { days: selected.horizon_days }) }}</span>
              </div>
              <div>
                <span>{{ $t('prophecy.subjects') }}</span>
                <b>{{ (selected.entities || []).join('、') || '—' }}</b>
              </div>
            </div>

            <router-link
              v-if="selected.source_report_id"
              :to="`/report/${selected.source_report_id}`"
              class="source-link"
              @click="closeDetail"
            >
              {{ $t('prophecy.sourceReport') }} ↗
            </router-link>
          </aside>

          <div class="detail-main">
            <span class="status-pill" :class="selected.status">
              {{ $t(statusKey(selected.status)) }}
            </span>
            <h2>{{ selected.title }}</h2>

            <div class="prophecy-box" :class="{ sealed: isSealed }">
              <p>{{ selected.prophecy }}</p>
              <div v-if="isSealed" class="seal-cover">
                <span class="seal-glyph">✦</span>
                <p>{{ $t('prophecy.sealedHint') }}</p>
                <button class="primary-btn" :disabled="opening" @click="doOpen">
                  {{ opening ? $t('prophecy.opening') : $t('prophecy.openOrb') }}
                </button>
              </div>
            </div>

            <p class="prophecy-summary">{{ selected.summary }}</p>

            <section class="detail-section">
              <h3>{{ $t('prophecy.branches') }}</h3>
              <div class="branch-grid">
                <article
                  v-for="(branch, index) in selected.branches"
                  :key="index"
                  class="branch-card"
                >
                  <div class="branch-top">
                    <b>{{ branch.name }}</b>
                    <span>{{ branch.likelihood }}%</span>
                  </div>
                  <div class="bar">
                    <span :style="{ width: `${branch.likelihood}%` }"></span>
                  </div>
                  <p v-if="branch.trigger" class="branch-trigger">
                    <em>{{ $t('prophecy.trigger') }}</em> {{ branch.trigger }}
                  </p>
                  <p v-if="branch.outcome" class="branch-outcome">{{ branch.outcome }}</p>
                </article>
              </div>
            </section>

            <section class="detail-section">
              <h3>{{ $t('prophecy.indicators') }}</h3>
              <ul class="indicator-list">
                <li v-for="(indicator, index) in selected.indicators" :key="index">
                  <span class="indicator-dot"></span>
                  <div>
                    <b>{{ indicator.signal }}</b>
                    <p v-if="indicator.threshold">{{ indicator.threshold }}</p>
                  </div>
                </li>
              </ul>
            </section>

            <section v-if="!isSealed" class="detail-section">
              <h3>{{ $t('prophecy.scryTitle') }}</h3>
              <div class="scry-log">
                <div
                  v-for="entry in selected.scryings || []"
                  :key="entry.scrying_id"
                  class="scry-entry"
                >
                  <p class="scry-question">{{ entry.question }}</p>
                  <p class="scry-answer">{{ entry.answer }}</p>
                </div>
                <p v-if="!(selected.scryings || []).length" class="muted">
                  {{ $t('prophecy.scryEmpty') }}
                </p>
              </div>
              <div class="inline-row">
                <input
                  v-model="scryQuestion"
                  class="field"
                  :placeholder="$t('prophecy.scryPlaceholder')"
                  @keyup.enter="doScry"
                />
                <button class="primary-btn" :disabled="scrying" @click="doScry">
                  {{ scrying ? $t('prophecy.scrying') : $t('prophecy.scryButton') }}
                </button>
              </div>
            </section>

            <section v-if="!isSealed" class="detail-section verify">
              <h3>{{ $t('prophecy.verifyTitle') }}</h3>
              <p class="section-desc">{{ $t('prophecy.verifyDesc') }}</p>
              <textarea
                v-model="verifyOutcome"
                class="field textarea"
                :placeholder="$t('prophecy.outcomePlaceholder')"
              ></textarea>
              <div class="inline-row wrap">
                <select v-model="verifyVerdict" class="field select-compact">
                  <option value="">{{ $t('prophecy.verdictAuto') }}</option>
                  <option value="fulfilled">{{ $t('prophecy.verdictFulfilled') }}</option>
                  <option value="partial">{{ $t('prophecy.verdictPartial') }}</option>
                  <option value="shattered">{{ $t('prophecy.verdictShattered') }}</option>
                </select>
                <input
                  v-model="verifyAccuracy"
                  class="field accuracy-input"
                  type="number"
                  min="0"
                  max="100"
                  :placeholder="$t('prophecy.accuracyLabel')"
                />
                <button class="primary-btn" :disabled="verifying" @click="doVerify">
                  {{ verifying ? $t('prophecy.verifying') : $t('prophecy.verifyButton') }}
                </button>
              </div>

              <div v-if="isTerminal" class="verified-box">
                <div class="verified-head">
                  <span>{{ $t('prophecy.verified') }}</span>
                  <b>{{ selected.accuracy }}%</b>
                </div>
                <p>{{ selected.outcome }}</p>
              </div>
            </section>

            <p class="backflow-note">{{ $t('prophecy.memoryBackflow') }}</p>
            <button class="ghost-btn danger" @click="doDelete">
              {{ $t('prophecy.deleteOrb') }}
            </button>
            <p v-if="detailError" class="notice error">{{ detailError }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import ArtifactLinks from '../components/ArtifactLinks.vue'
import ProphecyOrb from '../components/ProphecyOrb.vue'
import {
  deleteOrb,
  forgeOrb,
  getOrb,
  getOrbs,
  getProphecyOverview,
  getProphecyReminders,
  openOrb,
  scryOrb,
  verifyOrb
} from '../api/prophecy'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const overview = ref(null)
const orbs = ref([])
const reminders = ref({ days: 7, count: 0, overdue: 0, due_soon: 0, items: [] })
const loading = ref(true)
const error = ref('')
const selected = ref(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const filterStatus = ref('')
const forgeReportId = ref('')
const forging = ref(false)
const notice = ref('')
const opening = ref(false)
const scryQuestion = ref('')
const scrying = ref(false)
const verifyOutcome = ref('')
const verifyVerdict = ref('')
const verifyAccuracy = ref('')
const verifying = ref(false)

const calibration = computed(() => overview.value?.calibration || {
  n: 0, brier_score: null, log_loss: null, reliability: []
})

const stats = computed(() => overview.value?.stats || {
  total: 0, sealed: 0, opened: 0, fulfilled: 0, partial: 0,
  shattered: 0, average_accuracy: null
})

const availableReports = computed(() =>
  (overview.value?.reports || []).filter(report => !report.forged))

const statusFilters = computed(() => [
  { value: '', label: t('prophecy.filterAll') },
  { value: 'sealed', label: t('prophecy.statusSealed') },
  { value: 'opened', label: t('prophecy.statusOpened') },
  { value: 'fulfilled', label: t('prophecy.statusFulfilled') },
  { value: 'partial', label: t('prophecy.statusPartial') },
  { value: 'shattered', label: t('prophecy.statusShattered') }
])

const filteredOrbs = computed(() =>
  filterStatus.value
    ? orbs.value.filter(orb => orb.status === filterStatus.value)
    : orbs.value)

const reminderSummary = computed(() => {
  const { overdue, due_soon: due, days } = reminders.value
  if (overdue && due) return t('prophecy.reminderSummary', { overdue, due, days })
  if (overdue) return t('prophecy.reminderSummaryOverdue', { overdue })
  return t('prophecy.reminderSummaryDue', { due, days })
})

const isSealed = computed(() => selected.value?.status === 'sealed')
const isTerminal = computed(() =>
  ['fulfilled', 'partial', 'shattered'].includes(selected.value?.status))

const ringDash = computed(() => {
  const confidence = Math.max(0, Math.min(100, selected.value?.confidence || 0))
  const circumference = 2 * Math.PI * 52
  return `${(confidence / 100) * circumference} ${circumference}`
})

function statusKey(status) {
  const value = status || 'sealed'
  return `prophecy.status${value.charAt(0).toUpperCase()}${value.slice(1)}`
}

async function refreshOverview() {
  const response = await getProphecyOverview()
  overview.value = response.data
}

async function refreshOrbs() {
  const response = await getOrbs()
  orbs.value = response.data
}

async function refreshReminders() {
  try {
    const response = await getProphecyReminders(7)
    reminders.value = response.data
  } catch (err) {
    // 提醒是增强信息，接口不可用时不该把整个预言厅拖成错误页
    reminders.value = { days: 7, count: 0, overdue: 0, due_soon: 0, items: [] }
  }
}

function reminderDays(item) {
  const days = item.days_left
  if (days == null) return ''
  if (days < 0) return t('prophecy.reminderOverdue', { days: Math.abs(days) })
  if (days === 0) return t('prophecy.reminderToday')
  return t('prophecy.reminderDue', { days })
}

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([refreshOverview(), refreshOrbs(), refreshReminders()])
    if (!forgeReportId.value ||
        !availableReports.value.some(report => report.report_id === forgeReportId.value)) {
      forgeReportId.value = availableReports.value[0]?.report_id || ''
    }
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function runForge() {
  if (!forgeReportId.value || forging.value) return
  forging.value = true
  notice.value = ''
  try {
    const response = await forgeOrb(forgeReportId.value)
    if (response.data.created === false) {
      notice.value = t('prophecy.forgeExisting')
    } else {
      notice.value = response.data.used_llm
        ? t('prophecy.forgeDone')
        : t('prophecy.forgeFallback')
    }
    await loadAll()
    if (response.data.orb) await openDetail(response.data.orb)
  } catch (err) {
    notice.value = `${t('prophecy.forgeError')}: ${err.message}`
  } finally {
    forging.value = false
  }
}

async function openDetail(orb) {
  detailOpen.value = true
  detailLoading.value = true
  detailError.value = ''
  try {
    const response = await getOrb(orb.orb_id)
    selected.value = response.data
    verifyOutcome.value = response.data.outcome || ''
    verifyVerdict.value = isTerminal.value ? response.data.status : ''
    verifyAccuracy.value = response.data.accuracy ?? ''
    scryQuestion.value = ''
  } catch (err) {
    detailError.value = err.message
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detailOpen.value = false
  selected.value = null
  detailError.value = ''
}

async function doOpen() {
  if (!selected.value || opening.value) return
  opening.value = true
  detailError.value = ''
  try {
    const response = await openOrb(selected.value.orb_id)
    selected.value = response.data
    await Promise.all([refreshOverview(), refreshOrbs(), refreshReminders()])
  } catch (err) {
    detailError.value = err.message
  } finally {
    opening.value = false
  }
}

async function doScry() {
  const question = scryQuestion.value.trim()
  if (!selected.value || !question || scrying.value) return
  scrying.value = true
  detailError.value = ''
  try {
    const response = await scryOrb(selected.value.orb_id, question)
    selected.value = response.data.orb
    scryQuestion.value = ''
    await Promise.all([refreshOrbs(), refreshReminders()])
  } catch (err) {
    detailError.value = err.message
  } finally {
    scrying.value = false
  }
}

async function doVerify() {
  if (!selected.value || verifying.value) return
  if (!verifyOutcome.value.trim()) {
    detailError.value = t('prophecy.outcomeRequired')
    return
  }
  verifying.value = true
  detailError.value = ''
  try {
    const payload = {
      outcome: verifyOutcome.value.trim(),
      verdict: verifyVerdict.value || ''
    }
    if (verifyAccuracy.value !== '' && verifyAccuracy.value !== null) {
      payload.accuracy = Number(verifyAccuracy.value)
    }
    const response = await verifyOrb(selected.value.orb_id, payload)
    selected.value = response.data
    verifyVerdict.value = response.data.status
    verifyAccuracy.value = response.data.accuracy ?? ''
    await Promise.all([refreshOverview(), refreshOrbs(), refreshReminders()])
  } catch (err) {
    detailError.value = err.message
  } finally {
    verifying.value = false
  }
}

async function doDelete() {
  if (!selected.value) return
  if (!window.confirm(t('prophecy.confirmDeleteOrb'))) return
  try {
    await deleteOrb(selected.value.orb_id)
    closeDetail()
    await loadAll()
  } catch (err) {
    detailError.value = err.message
  }
}

onMounted(async () => {
  await loadAll()
  // 支持 /prophecy?orb=<id> 深链，方便从冥想盆或聊天里直接打开一颗球
  if (route.query.orb) await openDetail({ orb_id: route.query.orb })
})
</script>

<style scoped>
.prophecy-page {
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
  padding-bottom: 70px;
  overflow-x: hidden;
}

.hero-copy,
.hero-visual,
.stat-bar,
.forge-panel,
.forge-controls,
.hall,
.detail-card {
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

.header-links { flex: 1; justify-content: center; }

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

.header-right :deep(.switcher-option) { color: #333; }
.header-right :deep(.switcher-option:hover) { background: var(--gray-light); }

/* Hero */
.hero {
  max-width: 1280px;
  margin: 0 auto;
  padding: 64px 40px 32px;
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(300px, 0.92fr);
  gap: 34px;
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

.hero-visual {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  border: 1px solid var(--border);
  background:
    radial-gradient(#E5E5E5 1px, transparent 1px) 0 0 / 18px 18px,
    var(--surface);
}

.constellation { display: none; }

.empty-orb {
  width: 220px;
  height: 220px;
  border-radius: 50%;
  border: 1px dashed var(--border-strong);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--border-strong);
  font-size: 2rem;
  background: rgba(255, 255, 255, 0.6);
}

.stat-bar {
  max-width: 1200px;
  margin: 24px auto 0;
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border);
}

/* 到期提醒：只在有即将到期/已过期的球时出现 */
.reminder-bar {
  max-width: 1200px;
  margin: 24px auto 0;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  border: 1px solid #FFD2C2;
  border-left: 4px solid var(--orange);
  background: #FFF6F2;
}

.reminder-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.reminder-kicker {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--orange);
}

.reminder-copy p {
  color: #333;
  font-size: 0.78rem;
  line-height: 1.5;
}

.reminder-bar.overdue {
  border-color: #E0B9B4;
  border-left-color: #B23A2F;
  background: #FDF3F2;
}

.reminder-bar.overdue .reminder-kicker { color: #B23A2F; }

.reminder-items {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
  min-width: 0;
}

.reminder-item {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 280px;
  padding: 6px 9px;
  background: #fff;
  border: 1px solid var(--border);
  border-left: 2px solid var(--orange);
  color: var(--ink);
  font-family: inherit;
  font-size: 0.72rem;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}

.reminder-item:hover { border-color: var(--ink); }
.reminder-item.overdue,
.reminder-item.critical,
.reminder-item.today { border-left-color: #B23A2F; }

.reminder-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reminder-days {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.62rem;
  color: var(--orange);
  white-space: nowrap;
}

.reminder-item.overdue .reminder-days,
.reminder-item.critical .reminder-days,
.reminder-item.today .reminder-days { color: #B23A2F; }

.reminder-more {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--muted);
  align-self: center;
}

.stat-cell {
  background: #fff;
  padding: 16px 14px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.stat-cell b {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.42rem;
  line-height: 1;
  font-weight: 700;
  color: var(--ink);
}

.stat-cell b i { font-style: normal; font-size: 0.8rem; color: var(--gray-text); }

.stat-cell:last-child b { color: var(--orange); }

.stat-cell span {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.64rem;
  color: var(--muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.forge-panel {
  max-width: 1200px;
  margin: 28px auto 0;
  padding: 20px 24px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, auto);
  gap: 22px;
  align-items: center;
  border: 1px solid var(--border);
  border-left: 3px solid var(--orange);
  background: var(--surface);
}

.forge-copy h2 { font-size: 1.02rem; font-weight: 700; margin-bottom: 6px; color: var(--ink); }
.forge-copy p { color: var(--gray-text); font-size: 0.8rem; line-height: 1.6; }

.forge-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
  align-items: center;
  min-width: 0;
}

.forge-controls .field {
  flex: 1 1 220px;
  width: 0;
  min-width: 0;
  max-width: 100%;
  background: #fff;
  border: 1px solid var(--border-strong);
  color: var(--ink);
  padding: 10px 12px;
  font-family: inherit;
  font-size: 0.8rem;
  outline: none;
}

.forge-controls .field:focus { border-color: var(--ink); box-shadow: 0 0 0 1px var(--ink); }

.forge-empty {
  color: var(--muted);
  font-size: 0.78rem;
  text-align: right;
}

.notice {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 0.74rem;
  line-height: 1.55;
  color: var(--orange);
  border-left: 2px solid var(--orange);
  padding-left: 10px;
}

.notice.error { color: #B23A2F; border-color: #B23A2F; }

.hall {
  max-width: 1280px;
  margin: 38px auto 0;
  padding: 0 40px;
}

.hall-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}

.hall-head h2 { font-size: 1.1rem; font-weight: 700; margin-bottom: 5px; color: var(--ink); }
.hall-head p { color: var(--gray-text); font-size: 0.74rem; }

.status-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}

.filter-chip {
  background: #fff;
  border: 1px solid var(--border);
  color: var(--gray-text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.63rem;
  padding: 5px 9px;
  cursor: pointer;
  transition: all 0.18s;
}

.filter-chip:hover { color: var(--ink); border-color: var(--ink); }

.filter-chip.active {
  color: #fff;
  background: var(--ink);
  border-color: var(--ink);
  font-weight: 700;
}

.orb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 10px 14px;
}

.orb-cell {
  position: relative;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}

/* 旧版的紫色地面光晕在浅色页面里只会显脏，去掉 */
.orb-cell::after { display: none; }

.orb-cell :deep(.orb-card) {
  height: 100%;
  justify-content: flex-start;
}

.primary-btn {
  background: var(--ink);
  color: #fff;
  border: 1px solid var(--ink);
  padding: 10px 18px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 0.74rem;
  letter-spacing: 0.03em;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s, transform 0.2s;
  white-space: nowrap;
}

.primary-btn:hover:not(:disabled) {
  background: var(--orange);
  border-color: var(--orange);
  transform: translateY(-1px);
}

.primary-btn:disabled { opacity: 0.45; cursor: wait; }

.ghost-btn {
  background: #fff;
  border: 1px solid var(--border-strong);
  color: #333;
  padding: 8px 12px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  cursor: pointer;
  transition: all 0.2s;
}

.ghost-btn:hover { border-color: var(--ink); color: var(--ink); }
.ghost-btn.compact { padding: 6px 10px; font-size: 0.66rem; }
.ghost-btn.danger { margin-top: 12px; }
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

.empty {
  border: 1px dashed var(--border-strong);
  padding: 34px 18px;
  text-align: center;
  color: var(--muted);
  font-size: 0.82rem;
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
  text-decoration: underline;
  text-underline-offset: 2px;
}

.link-btn:hover { color: var(--orange); }

.muted { color: var(--muted); font-size: 0.76rem; }

.overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.detail-card {
  position: relative;
  width: min(1080px, 100%);
  max-height: 90vh;
  overflow: auto;
  background: #fff;
  border: 1px solid #000;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.28);
  padding: 28px;
}

.detail-loading { margin: 40px; }

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
  z-index: 2;
}

.close-btn:hover { color: var(--orange); border-color: var(--ink); }

.detail-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 30px;
}

.detail-left {
  display: flex;
  flex-direction: column;
  align-items: center;
  border-right: 1px solid var(--border);
  padding-right: 26px;
}

.confidence-ring {
  position: relative;
  width: 108px;
  height: 108px;
  margin: 6px 0 18px;
}

.confidence-ring svg { width: 100%; height: 100%; }

.ring-track {
  fill: none;
  stroke: var(--border);
  stroke-width: 5;
}

.ring-value {
  fill: none;
  stroke: var(--ink);
  stroke-width: 5;
  stroke-linecap: round;
  transition: stroke-dasharray 0.6s ease;
}

.ring-value.fulfilled { stroke: var(--orange); }
.ring-value.partial { stroke: #F59E0B; }
.ring-value.shattered { stroke: var(--muted); }

.ring-label {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
}

.ring-label b {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.3rem;
  color: var(--ink);
}

.ring-label span {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.58rem;
  color: var(--muted);
  letter-spacing: 0.08em;
}

.meta-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 9px;
  font-size: 0.72rem;
}

.meta-list > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--gray-light);
}

.meta-list span {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.58rem;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
}

.meta-list b { color: var(--ink); font-weight: 500; line-height: 1.45; }

.source-link {
  margin-top: 16px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--orange);
  text-decoration: none;
  border-bottom: 1px dashed var(--orange);
  padding-bottom: 2px;
}

.source-link:hover { color: var(--ink); border-color: var(--ink); }

.detail-main { min-width: 0; padding-top: 4px; }

.status-pill {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  letter-spacing: 0.08em;
  padding: 3px 9px;
  border: 1px solid var(--border-strong);
  color: var(--gray-text);
}

.status-pill.fulfilled { color: var(--orange); border-color: var(--orange); }
.status-pill.partial { color: #B45309; border-color: #F59E0B; }
.status-pill.shattered { color: var(--muted); border-color: var(--border-strong); }
.status-pill.opened { color: var(--ink); border-color: var(--ink); }

.detail-main h2 {
  font-size: 1.45rem;
  font-weight: 700;
  line-height: 1.3;
  margin: 10px 0 16px;
  color: var(--ink);
}

.prophecy-box {
  position: relative;
  padding: 18px 20px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--orange);
  background: var(--surface);
  min-height: 90px;
}

.prophecy-box > p {
  font-family: 'Noto Serif SC', Georgia, serif;
  font-size: 0.98rem;
  line-height: 1.95;
  color: #222;
  white-space: pre-wrap;
}

.prophecy-box.sealed > p { filter: blur(5px); opacity: 0.5; user-select: none; }

.seal-cover {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  text-align: center;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.6), rgba(250, 250, 250, 0.92));
  padding: 16px;
}

.seal-glyph {
  font-size: 1.4rem;
  color: var(--orange);
}

.seal-cover p {
  color: var(--gray-text);
  font-size: 0.8rem;
  max-width: 380px;
  line-height: 1.6;
}

.prophecy-summary {
  margin-top: 13px;
  color: var(--gray-text);
  font-size: 0.82rem;
  line-height: 1.7;
}

.detail-section { margin-top: 26px; }

.detail-section h3 {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 11px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--border);
}

.section-desc { color: var(--gray-text); font-size: 0.76rem; margin-bottom: 10px; }

.branch-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 9px;
}

.branch-card {
  border: 1px solid var(--border);
  background: #fff;
  padding: 12px;
}

.branch-top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.76rem;
  margin-bottom: 7px;
  color: var(--ink);
}

.branch-top span {
  font-family: 'JetBrains Mono', monospace;
  color: var(--orange);
  font-size: 0.68rem;
}

.bar {
  height: 3px;
  background: var(--gray-light);
  margin-bottom: 9px;
  overflow: hidden;
}

.bar span {
  display: block;
  height: 100%;
  background: var(--ink);
}

.branch-trigger,
.branch-outcome {
  color: var(--gray-text);
  font-size: 0.72rem;
  line-height: 1.55;
  margin-top: 5px;
}

.branch-trigger em {
  font-style: normal;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.58rem;
  letter-spacing: 0.06em;
  color: var(--muted);
  text-transform: uppercase;
  margin-right: 4px;
}

.indicator-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.indicator-list li {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  border-left: 1px solid var(--border);
  padding-left: 12px;
}

.indicator-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--orange);
  margin-top: 7px;
  flex: none;
}

.indicator-list b { font-size: 0.78rem; font-weight: 500; color: var(--ink); }
.indicator-list p { color: var(--gray-text); font-size: 0.71rem; margin-top: 3px; line-height: 1.5; }

.scry-log {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 11px;
  max-height: 240px;
  overflow: auto;
}

.scry-entry {
  border-left: 2px solid var(--orange);
  padding-left: 11px;
}

.scry-question {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--orange);
  margin-bottom: 4px;
}

.scry-answer {
  color: #333;
  font-size: 0.78rem;
  line-height: 1.65;
  white-space: pre-wrap;
}

.inline-row {
  display: flex;
  gap: 8px;
  align-items: stretch;
}

.inline-row.wrap { flex-wrap: wrap; }

.field {
  background: #fff;
  border: 1px solid var(--border-strong);
  color: var(--ink);
  padding: 9px 11px;
  font-family: inherit;
  font-size: 0.78rem;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.field:focus { border-color: var(--ink); box-shadow: 0 0 0 1px var(--ink); }
.inline-row .field { flex: 1; min-width: 0; }

.textarea {
  width: 100%;
  min-height: 82px;
  resize: vertical;
  line-height: 1.6;
  margin-bottom: 9px;
}

.select-compact { flex: 0 0 150px; }
.accuracy-input { flex: 0 0 190px; }

.verified-box {
  margin-top: 13px;
  border: 1px solid #FFD2C2;
  background: #FFF6F2;
  padding: 12px 14px;
}

.verified-head {
  display: flex;
  justify-content: space-between;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--orange);
  margin-bottom: 6px;
}

.verified-box p { color: #333; font-size: 0.78rem; line-height: 1.6; }

.backflow-note {
  margin-top: 22px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.62rem;
  letter-spacing: 0.06em;
  color: var(--muted);
}

@media (max-width: 980px) {
  .hero { grid-template-columns: minmax(0, 1fr); }
  .hero-visual { order: -1; min-height: 220px; }
  .stat-bar { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .forge-panel { grid-template-columns: 1fr; }
  .detail-layout { grid-template-columns: 1fr; }
  .detail-left {
    border-right: none;
    border-bottom: 1px solid var(--border);
    padding: 0 0 20px;
  }
  .meta-list {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .hall-head { flex-direction: column; align-items: flex-start; }
  .status-filters { justify-content: flex-start; }
  .reminder-bar { flex-direction: column; align-items: flex-start; }
  .reminder-items { justify-content: flex-start; }
}

@media (max-width: 640px) {
  .page-header { padding: 0 16px; }
  .header-right .ghost-btn.compact { display: none; }
  .hero, .hall { padding-left: 16px; padding-right: 16px; }
  .forge-controls { flex-direction: column; align-items: stretch; }
  .forge-controls .field, .forge-controls .primary-btn { width: 100%; flex: none; }
  .stat-bar { margin: 24px 16px 0; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .reminder-bar { margin: 20px 16px 0; padding: 14px 16px; }
  .forge-panel { margin-left: 16px; margin-right: 16px; padding: 16px; }
  .detail-card { padding: 18px; }
  .orb-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .inline-row { flex-wrap: wrap; }
  .select-compact, .accuracy-input { flex: 1 1 100%; }
}
</style>
