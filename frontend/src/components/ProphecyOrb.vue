<template>
  <div
    class="orb-card"
    :class="[orb.status, sizeClass, { interactive, 'meta-visible': showMeta }]"
    :title="orb.title"
    @click="interactive && $emit('open', orb)"
  >
    <div class="orb-stage">
      <div class="orb-halo"></div>
      <div class="orb">
        <div class="orb-core"></div>
        <div class="orb-sheen"></div>
        <div class="orb-rune">{{ rune }}</div>
        <div v-if="orb.status === 'shattered'" class="orb-cracks"></div>
        <div v-if="orb.status === 'opened'" class="orb-rings">
          <span></span><span></span><span></span>
        </div>
      </div>
      <div class="orb-base"></div>
    </div>

    <div v-if="showMeta" class="orb-meta">
      <div class="orb-title">{{ orb.title }}</div>
      <div class="orb-sub">
        <span class="status-tag">{{ $t(statusKey) }}</span>
        <span class="confidence">{{ $t('prophecy.confidence') }} {{ orb.confidence }}%</span>
      </div>
      <div v-if="orb.deadline" class="orb-deadline" :class="deadlineState">
        {{ orb.deadline }}
        <span v-if="deadlineText" class="deadline-badge">{{ deadlineText }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  orb: { type: Object, required: true },
  size: { type: String, default: 'md' }, // sm | md | lg | xl
  showMeta: { type: Boolean, default: true },
  interactive: { type: Boolean, default: true }
})

defineEmits(['open'])

const sizeClass = computed(() => `size-${props.size}`)
const rune = computed(() => {
  const runes = ['✦', '◈', '✧', '❖', '✺']
  const seed = (props.orb.orb_id || props.orb.title || '').split('')
    .reduce((sum, char) => sum + char.charCodeAt(0), 0)
  return runes[seed % runes.length]
})
const statusKey = computed(() => {
  const status = props.orb.status || 'sealed'
  return `prophecy.status${status.charAt(0).toUpperCase()}${status.slice(1)}`
})

const terminalStatuses = ['fulfilled', 'partial', 'shattered']

const daysLeft = computed(() => {
  if (props.orb.days_left !== null && props.orb.days_left !== undefined) {
    return props.orb.days_left
  }
  if (!props.orb.deadline) return null
  const deadline = new Date(`${String(props.orb.deadline).slice(0, 10)}T00:00:00`)
  if (Number.isNaN(deadline.getTime())) return null
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((deadline - today) / 86400000)
})

const deadlineState = computed(() => {
  if (terminalStatuses.includes(props.orb.status)) return ''
  if (props.orb.deadline_state) return props.orb.deadline_state
  const days = daysLeft.value
  if (days === null) return ''
  if (days < 0) return 'overdue'
  if (days === 0) return 'today'
  if (days <= 3) return 'critical'
  if (days <= 7) return 'soon'
  return 'upcoming'
})

const deadlineText = computed(() => {
  const state = deadlineState.value
  if (!state || state === 'upcoming') return ''
  const days = daysLeft.value
  if (days === null) return ''
  if (days < 0) return t('prophecy.reminderOverdue', { days: Math.abs(days) })
  if (days === 0) return t('prophecy.reminderToday')
  return t('prophecy.reminderDue', { days })
})
</script>

<style scoped>
.orb-card {
  --orb-size: 132px;
  --orb-core: #F7F9FC;
  --orb-mid: #C3CCD9;
  --orb-edge: #3F4A5A;
  --orb-glow: rgba(0, 0, 0, 0.12);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 10px 8px 14px;
  cursor: default;
}

.orb-card.interactive { cursor: pointer; }
.orb-card.interactive:hover .orb { transform: translateY(-4px) scale(1.03); }
.orb-card.interactive:hover .orb-halo { opacity: 1; transform: scale(1.12); }

.size-sm { --orb-size: 82px; gap: 9px; }
.size-md { --orb-size: 132px; }
.size-lg { --orb-size: 186px; }
.size-xl { --orb-size: 240px; }

.orb-stage {
  position: relative;
  width: var(--orb-size);
  height: var(--orb-size);
  display: flex;
  align-items: center;
  justify-content: center;
}

.orb-halo {
  position: absolute;
  inset: -34%;
  border-radius: 50%;
  background: radial-gradient(circle, var(--orb-glow) 0%, transparent 62%);
  opacity: 0.7;
  filter: blur(5px);
  transition: opacity 0.35s, transform 0.35s;
  animation: orb-breathe 5.4s ease-in-out infinite;
}

.orb {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  overflow: hidden;
  border: 1px solid rgba(0, 0, 0, 0.32);
  background:
    radial-gradient(circle at 30% 24%, rgba(255, 255, 255, 1) 0 3%, rgba(255, 255, 255, 0.5) 10%, transparent 30%),
    radial-gradient(circle at 52% 58%, var(--orb-core) 0%, var(--orb-mid) 52%, var(--orb-edge) 100%);
  box-shadow:
    inset -8px -12px 24px rgba(0, 0, 0, 0.22),
    inset 8px 10px 20px rgba(255, 255, 255, 0.55),
    0 10px 26px rgba(0, 0, 0, 0.14);
  transition: transform 0.35s cubic-bezier(0.22, 0.75, 0.3, 1), box-shadow 0.35s;
}

.orb-core {
  position: absolute;
  left: 50%;
  top: 52%;
  width: 52%;
  height: 52%;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.85) 0%, transparent 64%);
  filter: blur(4px);
  opacity: 0.85;
}

.orb-sheen {
  position: absolute;
  left: 16%;
  top: 10%;
  width: 44%;
  height: 26%;
  border-radius: 50%;
  background: linear-gradient(140deg, rgba(255, 255, 255, 0.95), rgba(255, 255, 255, 0));
  transform: rotate(-24deg);
  filter: blur(1px);
  opacity: 0.9;
}

.orb-rune {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: calc(var(--orb-size) * 0.2);
  color: rgba(255, 255, 255, 0.9);
  text-shadow: 0 1px 6px rgba(0, 0, 0, 0.35);
  pointer-events: none;
}

.orb-cracks {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  opacity: 0.8;
  background:
    linear-gradient(116deg, transparent 46%, rgba(255, 255, 255, 0.85) 47%, transparent 48%),
    linear-gradient(64deg, transparent 58%, rgba(255, 255, 255, 0.7) 59%, transparent 60%),
    linear-gradient(152deg, transparent 34%, rgba(255, 255, 255, 0.6) 35%, transparent 36%),
    linear-gradient(24deg, transparent 66%, rgba(255, 255, 255, 0.65) 67%, transparent 68%),
    radial-gradient(circle at 50% 52%, transparent 0 27%, rgba(0, 0, 0, 0.2) 28%, transparent 30%);
  mix-blend-mode: screen;
}

.orb-rings {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  overflow: hidden;
}

.orb-rings span {
  position: absolute;
  left: 50%;
  top: 66%;
  width: 70%;
  height: 22%;
  border: 1px solid rgba(0, 0, 0, 0.28);
  border-radius: 50%;
  transform: translate(-50%, -50%);
  animation: orb-ring 5s linear infinite;
}

.orb-rings span:nth-child(2) { width: 46%; animation-delay: -1.6s; opacity: 0.72; }
.orb-rings span:nth-child(3) { width: 26%; animation-delay: -3.2s; opacity: 0.5; }

.orb-base {
  position: absolute;
  bottom: -8%;
  left: 50%;
  width: 58%;
  height: 12%;
  transform: translateX(-50%);
  border-radius: 50%;
  background: radial-gradient(ellipse, rgba(0, 0, 0, 0.22), transparent 70%);
  filter: blur(3px);
}

/* 状态配色：银灰玻璃 + 橙色应验 */
.orb-card.sealed {
  --orb-core: #F4F6F9;
  --orb-mid: #B9C2CE;
  --orb-edge: #4B5563;
  --orb-glow: rgba(0, 0, 0, 0.1);
}
.orb-card.sealed .orb-rune { opacity: 0.55; }

.orb-card.opened {
  --orb-core: #F2F5F8;
  --orb-mid: #A9B4C2;
  --orb-edge: #1F2937;
  --orb-glow: rgba(0, 0, 0, 0.18);
}

.orb-card.fulfilled {
  --orb-core: #FFF7EA;
  --orb-mid: #FFB066;
  --orb-edge: #B34700;
  --orb-glow: rgba(255, 69, 0, 0.4);
}
.orb-card.fulfilled .orb-halo { animation-duration: 3.6s; }

.orb-card.partial {
  --orb-core: #FFEDD5;
  --orb-mid: #F59E0B;
  --orb-edge: #92400E;
  --orb-glow: rgba(245, 158, 11, 0.32);
}

.orb-card.shattered {
  --orb-core: #E5E7EB;
  --orb-mid: #9CA3AF;
  --orb-edge: #4B5563;
  --orb-glow: rgba(0, 0, 0, 0.08);
}
.orb-card.shattered .orb { filter: saturate(0.2) brightness(0.95); }
.orb-card.shattered .orb-halo { opacity: 0.22; animation: none; }

.orb-meta {
  width: 100%;
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.orb-title {
  font-family: 'Space Grotesk', 'Noto Sans SC', sans-serif;
  font-size: 0.93rem;
  font-weight: 700;
  color: #000000;
  line-height: 1.35;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.orb-sub {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.64rem;
  color: #999999;
}

.status-tag {
  padding: 1px 7px;
  border: 1px solid #CCCCCC;
  color: #666666;
}

.orb-card.fulfilled .status-tag { color: #FF4500; border-color: #FF4500; }
.orb-card.partial .status-tag { color: #B45309; border-color: #F59E0B; }
.orb-card.shattered .status-tag { color: #999999; border-color: #CCCCCC; }
.orb-card.opened .status-tag { color: #000000; border-color: #000000; }

.orb-deadline {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.62rem;
  color: #999999;
}

.orb-deadline.overdue,
.orb-deadline.critical,
.orb-deadline.today { color: #B23A2F; }

.orb-deadline.soon { color: #B45309; }

.deadline-badge {
  padding: 1px 5px;
  border: 1px solid currentColor;
  font-size: 0.56rem;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

@keyframes orb-breathe {
  0%, 100% { opacity: 0.5; transform: scale(0.97); }
  50% { opacity: 0.85; transform: scale(1.05); }
}

@keyframes orb-ring {
  from { transform: translate(-50%, -50%) scale(0.7); opacity: 0.7; }
  to { transform: translate(-50%, -50%) scale(1.25); opacity: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .orb-halo, .orb-rings span { animation: none; }
}
</style>
