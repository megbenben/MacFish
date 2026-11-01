<template>
  <div class="artifact-links" :class="variant">
    <router-link
      to="/pensieve"
      class="artifact-link"
      :class="{ active: route.path === '/pensieve' }"
    >
      <span class="glyph pensieve-glyph">◉</span>
      <span class="label">{{ $t('nav.pensieve') }}</span>
    </router-link>
    <router-link
      to="/prophecy"
      class="artifact-link"
      :class="{ active: route.path === '/prophecy' }"
    >
      <span class="glyph prophecy-glyph">✦</span>
      <span class="label">{{ $t('nav.prophecy') }}</span>
      <span v-if="reminderCount > 0" class="reminder-badge">
        {{ reminderCount > 9 ? '9+' : reminderCount }}
      </span>
    </router-link>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getProphecyReminders } from '../api/prophecy'

defineProps({
  variant: {
    type: String,
    default: 'light' // light header (white bg) | dark nav (black bg)
  }
})

const route = useRoute()

// 到期提醒角标：进入任意页面时拉一次，之后每 10 分钟刷新一次。
// 后端不可用时静默降级，不打扰导航。
const reminderCount = ref(0)
let reminderTimer = null

async function loadReminders() {
  try {
    const response = await getProphecyReminders(7)
    reminderCount.value = response.data?.count || 0
  } catch (err) {
    reminderCount.value = 0
  }
}

onMounted(() => {
  loadReminders()
  reminderTimer = setInterval(loadReminders, 10 * 60 * 1000)
})

onUnmounted(() => {
  if (reminderTimer) clearInterval(reminderTimer)
})
</script>

<style scoped>
.artifact-links {
  display: flex;
  align-items: center;
  gap: 6px;
}

.artifact-link {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 11px;
  border: 1px solid transparent;
  text-decoration: none;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.76rem;
  letter-spacing: 0.02em;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
  white-space: nowrap;
}

.artifact-link {
  position: relative;
}

.artifact-link .glyph {
  font-family: sans-serif;
  font-size: 0.9rem;
  line-height: 1;
}

.reminder-badge {
  position: absolute;
  top: -7px;
  right: -7px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border: 1px solid #fff;
  border-radius: 9px;
  background: #FF4500;
  color: #fff;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  font-weight: 700;
  line-height: 14px;
  text-align: center;
  pointer-events: none;
}

.pensieve-glyph { color: #FF4500; }
.prophecy-glyph { color: #FF4500; }

/* 深色导航（首页黑底导航） */
.artifact-links.dark .artifact-link {
  color: rgba(255, 255, 255, 0.72);
  border-color: rgba(255, 255, 255, 0.14);
}

.artifact-links.dark .artifact-link:hover,
.artifact-links.dark .artifact-link.active {
  color: #fff;
  border-color: rgba(255, 255, 255, 0.5);
  background: rgba(255, 255, 255, 0.06);
}

/* 浅色页头（工作台白底） */
.artifact-links.light .artifact-link {
  color: #666;
  border-color: #e2e2e2;
}

.artifact-links.light .artifact-link:hover,
.artifact-links.light .artifact-link.active {
  color: #000;
  border-color: #000;
  background: #f7f7f7;
}

@media (max-width: 640px) {
  .artifact-link .label { display: none; }
}
</style>
