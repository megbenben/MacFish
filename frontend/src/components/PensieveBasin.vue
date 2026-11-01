<template>
  <div class="basin" :style="{ '--basin-size': size + 'px' }">
    <svg viewBox="0 0 400 400" class="basin-svg" role="img" :aria-label="$t('pensieve.title')">
      <defs>
        <radialGradient id="basin-halo" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="#FF4500" stop-opacity=".14" />
          <stop offset="55%" stop-color="#9AA2AC" stop-opacity=".1" />
          <stop offset="100%" stop-color="#FFFFFF" stop-opacity="0" />
        </radialGradient>
        <linearGradient id="basin-bowl" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#D9DDE2" />
          <stop offset="55%" stop-color="#A8B0BA" />
          <stop offset="100%" stop-color="#6B7480" />
        </linearGradient>
        <linearGradient id="basin-rim" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#8A929C" />
          <stop offset="18%" stop-color="#FFFFFF" />
          <stop offset="42%" stop-color="#C6CCD4" />
          <stop offset="68%" stop-color="#FFFFFF" />
          <stop offset="100%" stop-color="#7A828C" />
        </linearGradient>
        <radialGradient id="basin-liquid" cx="48%" cy="38%" r="68%">
          <stop offset="0%" stop-color="#FFFFFF" stop-opacity=".95" />
          <stop offset="28%" stop-color="#EEF2F6" stop-opacity=".85" />
          <stop offset="62%" stop-color="#C3CCD8" stop-opacity=".8" />
          <stop offset="100%" stop-color="#7C8797" stop-opacity=".92" />
        </radialGradient>
        <linearGradient id="strand-silver" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#F5F5F5" stop-opacity=".1" />
          <stop offset="45%" stop-color="#9AA4B2" stop-opacity=".75" />
          <stop offset="100%" stop-color="#6B7280" stop-opacity=".3" />
        </linearGradient>
        <linearGradient id="strand-gold" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#FFE8DE" stop-opacity=".1" />
          <stop offset="50%" stop-color="#FF7A3D" stop-opacity=".75" />
          <stop offset="100%" stop-color="#FF4500" stop-opacity=".25" />
        </linearGradient>
        <linearGradient id="basin-sheen" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#ffffff" stop-opacity=".55" />
          <stop offset="45%" stop-color="#ffffff" stop-opacity=".05" />
          <stop offset="100%" stop-color="#ffffff" stop-opacity="0" />
        </linearGradient>
        <filter id="basin-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="2.4" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <ellipse class="halo" cx="200" cy="190" rx="176" ry="138" fill="url(#basin-halo)" />

      <g class="strand-field" filter="url(#basin-glow)">
        <path
          v-for="(strand, index) in strands"
          :key="'strand-' + index"
          :d="strand.path"
          fill="none"
          :stroke="strand.stroke"
          :stroke-width="strand.width"
          stroke-linecap="round"
          class="strand"
          :style="{ animationDelay: `${index * -0.65}s` }"
        />
        <circle
          v-for="(dot, index) in dots"
          :key="'dot-' + index"
          :cx="dot.x"
          :cy="dot.y"
          :r="dot.r"
          fill="#FF4500"
          class="memory-dot"
          :style="{ animationDelay: `${index * -0.9}s` }"
        />
      </g>

      <ellipse cx="200" cy="300" rx="148" ry="28" fill="#000000" opacity=".12" class="basin-shadow" />
      <path d="M38 214 Q48 302 200 330 Q352 302 362 214 Q200 258 38 214 Z"
            fill="url(#basin-bowl)" stroke="#8A929C" stroke-width="1.2" />
      <ellipse cx="200" cy="216" rx="162" ry="48" fill="url(#basin-rim)" />
      <ellipse cx="200" cy="218" rx="140" ry="37" fill="url(#basin-liquid)" class="liquid" />

      <g class="swirl" clip-path="none">
        <ellipse cx="200" cy="219" rx="96" ry="22" fill="none"
                 stroke="#999999" stroke-opacity=".4" stroke-width="1" stroke-dasharray="18 14" />
        <ellipse cx="200" cy="219" rx="66" ry="14" fill="none"
                 stroke="#FF4500" stroke-opacity=".38" stroke-width=".9" stroke-dasharray="8 10" />
        <ellipse cx="200" cy="219" rx="112" ry="29" fill="none"
                 stroke="#666666" stroke-opacity=".25" stroke-width="1.4" stroke-dasharray="3 16" />
        <path d="M120 219 Q200 194 280 219 Q200 244 120 219 Z"
              fill="none" stroke="#CCCCCC" stroke-width="1" />
      </g>

      <ellipse cx="200" cy="210" rx="140" ry="37" fill="url(#basin-sheen)" opacity=".5" />

      <text v-if="selected" x="200" y="222" text-anchor="middle" class="basin-label">
        {{ shortTitle }}
      </text>
    </svg>

    <div class="basin-caption">
      <span class="caption-dot" :class="{ active: !!selected }"></span>
      <span class="caption-text">{{ selected ? selected.title : $t('pensieve.basinIdle') }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  size: { type: Number, default: 420 },
  memories: { type: Array, default: () => [] },
  selected: { type: Object, default: null }
})

const strands = computed(() => {
  const count = Math.max(6, Math.min(12, (props.memories || []).length || 8))
  return Array.from({ length: count }, (_, index) => {
    const angle = (index / count) * Math.PI * 2
    const startX = 200 + Math.cos(angle) * 156
    const startY = 186 + Math.sin(angle) * 118
    const controlX = 200 + Math.cos(angle + 0.42) * 92
    const controlY = 172 + Math.sin(angle + 0.42) * 62
    const endX = 200 + Math.cos(angle + 0.95) * 46
    const endY = 214 + Math.sin(angle + 0.95) * 15
    return {
      path: `M ${startX.toFixed(1)} ${startY.toFixed(1)} Q ${controlX.toFixed(1)} ${controlY.toFixed(1)} ${endX.toFixed(1)} ${endY.toFixed(1)}`,
      stroke: index % 4 === 0 ? 'url(#strand-gold)' : 'url(#strand-silver)',
      width: 0.8 + (index % 3) * 0.45
    }
  })
})

const dots = computed(() =>
  (props.memories || []).slice(0, 10).map((memory, index) => {
    const angle = (index / 10) * Math.PI * 2 + 0.3
    return {
      x: 200 + Math.cos(angle) * (92 + (index % 3) * 18),
      y: 176 + Math.sin(angle) * (52 + (index % 3) * 10),
      r: 1.2 + (memory.importance || 50) / 60
    }
  })
)

const shortTitle = computed(() => {
  const title = props.selected?.title || ''
  return title.length > 18 ? title.slice(0, 18) + '…' : title
})
</script>

<style scoped>
.basin {
  width: min(var(--basin-size), 100%);
  max-width: 100%;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  user-select: none;
}

.basin-svg {
  width: 100%;
  height: auto;
  overflow: visible;
}

.halo {
  animation: basin-breathe 7s ease-in-out infinite;
  transform-box: view-box;
  transform-origin: 200px 190px;
}

.strand {
  stroke-dasharray: 7 11;
  opacity: 0.7;
  animation: strand-flow 10s linear infinite;
}

.memory-dot {
  opacity: 0.8;
  animation: dot-float 5.5s ease-in-out infinite;
}

.liquid {
  animation: liquid-pulse 6s ease-in-out infinite;
  transform-box: view-box;
  transform-origin: 200px 218px;
}

.swirl {
  transform-box: view-box;
  transform-origin: 200px 219px;
  animation: basin-swirl 26s linear infinite;
}

.basin-shadow {
  filter: blur(3px);
}

.basin-label {
  font-family: 'JetBrains Mono', 'Noto Sans SC', monospace;
  font-size: 8.5px;
  letter-spacing: 0.08em;
  fill: rgba(0, 0, 0, 0.66);
  font-weight: 700;
}

.basin-caption {
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 9px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  color: #666666;
  letter-spacing: 0.04em;
  width: 100%;
  max-width: 100%;
  min-width: 0;
}

.caption-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #CCCCCC;
  flex: none;
}

.caption-dot.active {
  background: #FF4500;
  animation: basin-breathe 2.4s ease-in-out infinite;
}

.caption-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@keyframes basin-swirl {
  to { transform: rotate(360deg); }
}

@keyframes strand-flow {
  to { stroke-dashoffset: -72; }
}

@keyframes basin-breathe {
  0%, 100% { opacity: 0.55; transform: scale(0.97); }
  50% { opacity: 1; transform: scale(1.03); }
}

@keyframes dot-float {
  0%, 100% { opacity: 0.4; transform: translateY(0); }
  50% { opacity: 1; transform: translateY(-5px); }
}

@keyframes liquid-pulse {
  0%, 100% { opacity: 0.92; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.015); }
}

@media (prefers-reduced-motion: reduce) {
  .halo, .strand, .memory-dot, .liquid, .swirl, .caption-dot.active {
    animation: none;
  }
}
</style>
