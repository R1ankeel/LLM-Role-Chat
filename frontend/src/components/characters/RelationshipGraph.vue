<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import type { RelationshipEdge, RelationshipGraphNode } from '@/types/relationship'
import { relationshipTypeLabel } from '@/types/relationship'

const props = defineProps<{
  graph: { characters: RelationshipGraphNode[]; edges: RelationshipEdge[] }
  selectedEdgeId: number | null
}>()

const emit = defineEmits<{
  selectEdge: [edge: RelationshipEdge]
}>()

const REL_NODE_R = 26
const GRAPH_W = 720
const GRAPH_H = 460

function circularLayout(count: number) {
  const cx = GRAPH_W / 2
  const cy = GRAPH_H / 2
  const r = Math.min(GRAPH_W, GRAPH_H) / 2 - 70
  const pts: { x: number; y: number }[] = []
  for (let i = 0; i < count; i++) {
    const angle = (2 * Math.PI * i) / count - Math.PI / 2
    pts.push({ x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) })
  }
  return pts
}

const positions = reactive<Record<number, { x: number; y: number }>>({})

const nodes = computed(() => {
  const pts = circularLayout(props.graph.characters.length)
  return props.graph.characters.map((c, i) => {
    const existing = positions[c.id]
    const pos = existing ?? pts[i] ?? { x: GRAPH_W / 2, y: GRAPH_H / 2 }
    positions[c.id] = pos
    return { char: c, pos }
  })
})

function edgeClass(e: RelationshipEdge): string {
  const neg = e.resentment + e.jealousy - (e.affection + e.trust)
  if (e.attraction >= 60) return 'edge-rom'
  if (neg >= 40) return 'edge-neg'
  if (e.affection + e.trust >= 120) return 'edge-pos'
  return 'edge-neu'
}

interface Geo {
  d?: string
  x1?: number
  y1?: number
  x2?: number
  y2?: number
  tip: { x: number; y: number }
  ux: number
  uy: number
  labelX: number
  labelY: number
}

function arrowPoints(tip: { x: number; y: number }, ux: number, uy: number): string {
  const nx = -uy
  const ny = ux
  const p1 = { x: tip.x - ux * 14 + nx * 5, y: tip.y - uy * 14 + ny * 5 }
  const p2 = { x: tip.x - ux * 14 - nx * 5, y: tip.y - uy * 14 - ny * 5 }
  return `${tip.x.toFixed(1)},${tip.y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`
}

function quadPoint(
  p0: { x: number; y: number },
  c: { x: number; y: number },
  p1: { x: number; y: number },
  t: number,
) {
  const u = 1 - t
  return {
    x: u * u * p0.x + 2 * u * t * c.x + t * t * p1.x,
    y: u * u * p0.y + 2 * u * t * c.y + t * t * p1.y,
  }
}

function geometryFor(e: RelationshipEdge, reverse: RelationshipEdge | undefined): Geo {
  const s = positions[e.source_character_id]
  const t = positions[e.target_character_id]
  if (!s || !t) return { tip: { x: 0, y: 0 }, ux: 0, uy: 0, labelX: 0, labelY: 0 }

  if (reverse) {
    const mx = (s.x + t.x) / 2
    const my = (s.y + t.y) / 2
    let nx = -(t.y - s.y)
    let ny = t.x - s.x
    const nlen = Math.hypot(nx, ny) || 1
    nx /= nlen
    ny /= nlen
    const off = 26
    const dir = reverse.id < e.id ? 1 : -1
    const c = { x: mx + nx * off * dir, y: my + ny * off * dir }
    const tip = quadPoint(s, c, t, 0.94)
    const just = quadPoint(s, c, t, 0.9)
    const ux = tip.x - just.x
    const uy = tip.y - just.y
    const ulen = Math.hypot(ux, uy) || 1
    return {
      d: `M ${s.x.toFixed(1)} ${s.y.toFixed(1)} Q ${c.x.toFixed(1)} ${c.y.toFixed(1)} ${t.x.toFixed(1)} ${t.y.toFixed(1)}`,
      tip,
      ux: ux / ulen,
      uy: uy / ulen,
      labelX: c.x + nx * 10,
      labelY: c.y + ny * 10,
    }
  }

  const dx = t.x - s.x
  const dy = t.y - s.y
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const tip = { x: t.x - ux * REL_NODE_R, y: t.y - uy * REL_NODE_R }
  return {
    x1: s.x + ux * REL_NODE_R,
    y1: s.y + uy * REL_NODE_R,
    x2: tip.x,
    y2: tip.y,
    tip,
    ux,
    uy,
    labelX: (s.x + t.x) / 2 + ux * 10,
    labelY: (s.y + t.y) / 2 + uy * 10,
  }
}

const edges = computed(() =>
  props.graph.edges
    .map((e) => {
      const s = positions[e.source_character_id]
      const t = positions[e.target_character_id]
      if (!s || !t) return null
      const reverse = props.graph.edges.find(
        (x) =>
          x.source_character_id === e.target_character_id &&
          x.target_character_id === e.source_character_id,
      )
      return {
        edge: e,
        reverse: reverse ?? undefined,
        geo: geometryFor(e, reverse),
      }
    })
    .filter((e): e is NonNullable<typeof e> => e !== null),
)

const selectedCharId = ref<number | null>(null)
const drag = ref<number | null>(null)

function nodeClass(char: RelationshipGraphNode): string {
  const base = char.is_player ? 'rel-node rel-node-player' : 'rel-node rel-node-npc'
  return selectedCharId.value === char.id ? `${base} selected` : base
}

function edgeGroupClass(e: RelationshipEdge): string {
  const selected = props.selectedEdgeId === e.id
  return `rel-edge ${edgeClass(e)}${selected ? ' selected' : ''}`
}

function nodeDisplayName(name: string): string {
  return name.length > 12 ? `${name.slice(0, 12)}…` : name
}

function onNodeClick(char: RelationshipGraphNode) {
  selectedCharId.value = selectedCharId.value === char.id ? null : char.id
}

function onEdgeClick(e: RelationshipEdge) {
  selectedCharId.value = null
  emit('selectEdge', e)
}

const svgRef = ref<SVGSVGElement | null>(null)

function onMouseDown(e: MouseEvent) {
  const target = e.target as Element
  const nodeEl = target.closest('.rel-node')
  if (!nodeEl) return
  e.preventDefault()
  drag.value = Number(nodeEl.getAttribute('data-char-id'))
}

function onMouseMove(e: MouseEvent) {
  if (drag.value == null || !svgRef.value) return
  const rect = svgRef.value.getBoundingClientRect()
  const sx = GRAPH_W / rect.width
  const sy = GRAPH_H / rect.height
  const pos = positions[drag.value]
  if (!pos) return
  pos.x = Math.max(REL_NODE_R, Math.min(GRAPH_W - REL_NODE_R, (e.clientX - rect.left) * sx))
  pos.y = Math.max(REL_NODE_R, Math.min(GRAPH_H - REL_NODE_R, (e.clientY - rect.top) * sy))
}

function onMouseUp() {
  drag.value = null
}
</script>

<template>
  <div
    class="rel-graph"
    @mousedown="onMouseDown"
    @mousemove="onMouseMove"
    @mouseup="onMouseUp"
    @mouseleave="onMouseUp"
  >
    <svg
      v-if="nodes.length"
      ref="svgRef"
      class="rel-graph__svg"
      :viewBox="`0 0 ${GRAPH_W} ${GRAPH_H}`"
      xmlns="http://www.w3.org/2000/svg"
    >
      <g
        v-for="{ edge, geo } in edges"
        :key="edge.id"
        :class="edgeGroupClass(edge)"
        :data-rel-id="edge.id"
        role="button"
        tabindex="0"
        @click="onEdgeClick(edge)"
        @keydown.enter="onEdgeClick(edge)"
      >
        <path v-if="geo.d" :d="geo.d" fill="none" stroke="currentColor" />
        <line
          v-else
          :x1="geo.x1"
          :y1="geo.y1"
          :x2="geo.x2"
          :y2="geo.y2"
          stroke="currentColor"
        />
        <polygon :points="arrowPoints(geo.tip, geo.ux, geo.uy)" />
        <text class="rel-graph__edge-label" :x="geo.labelX" :y="geo.labelY">
          {{ relationshipTypeLabel(edge.relationship_type) }}{{ edge.open_issue_count ? ' ⚠' : '' }}
        </text>
      </g>

      <g
        v-for="{ char, pos } in nodes"
        :key="char.id"
        :class="nodeClass(char)"
        :data-char-id="char.id"
        :transform="`translate(${pos.x.toFixed(1)}, ${pos.y.toFixed(1)})`"
        role="button"
        tabindex="0"
        @click="onNodeClick(char)"
        @keydown.enter="onNodeClick(char)"
      >
        <circle :r="REL_NODE_R" />
        <text class="rel-graph__node-label" y="4">{{ nodeDisplayName(char.name) }}</text>
      </g>
    </svg>
    <p v-else class="rel-graph__hint">Нет персонажей.</p>
  </div>
</template>

<style scoped>
.rel-graph {
  position: relative;
  width: 100%;
  user-select: none;
}

.rel-graph__svg {
  width: 100%;
  height: auto;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.rel-graph__hint {
  font-size: var(--text-sm);
  color: var(--text-muted);
  padding: var(--space-4);
  text-align: center;
}

.rel-edge {
  cursor: pointer;
}

.rel-edge .rel-graph__edge-label {
  font-size: 11px;
  paint-order: stroke;
  stroke: var(--bg-primary);
  stroke-width: 3px;
  fill: var(--text-secondary);
  text-anchor: middle;
  pointer-events: none;
}

.rel-edge.edge-pos {
  color: var(--success);
}

.rel-edge.edge-pos .rel-graph__edge-label {
  fill: var(--success);
}

.rel-edge.edge-neg {
  color: var(--danger);
}

.rel-edge.edge-neg .rel-graph__edge-label {
  fill: var(--danger);
}

.rel-edge.edge-rom {
  color: #d1607a;
}

.rel-edge.edge-rom .rel-graph__edge-label {
  fill: #d1607a;
}

.rel-edge.edge-neu {
  color: var(--text-muted);
}

.rel-edge.selected {
  color: var(--accent);
}

.rel-edge.selected .rel-graph__edge-label {
  fill: var(--accent);
  font-weight: 600;
}

.rel-node {
  cursor: grab;
}

.rel-node:active {
  cursor: grabbing;
}

.rel-node circle {
  stroke: var(--border-strong);
  stroke-width: 2;
}

.rel-node-npc circle {
  fill: #232c3f;
}

.rel-node-player circle {
  fill: rgba(108, 140, 255, 0.22);
  stroke: var(--accent);
}

.rel-node.selected circle {
  stroke: var(--accent);
  stroke-width: 3;
}

.rel-node .rel-graph__node-label {
  font-size: 11px;
  text-anchor: middle;
  fill: var(--text-primary);
  pointer-events: none;
}
</style>
