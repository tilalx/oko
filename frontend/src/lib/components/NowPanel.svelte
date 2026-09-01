<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { rgbForIntensity, rgbToHex, readableTextColor } from '$lib/color'
  import { formatWeekdayTime, countryFlagEmoji } from '$lib/format'
  import { CATEGORY_META, CATEGORY_ORDER } from '$lib/constants'
  import Gauge from './Gauge.svelte'
  import { Tabs } from '$lib/components/ui/tabs'
  import { Tooltip } from '$lib/components/ui/tooltip'
  import type { CurrentBreakdown, ForecastPoint, HistoryPoint } from '$lib/api'

  function dominantSource(breakdown: Record<string, number> | undefined | null) {
    if (!breakdown) return null
    let best: { category: string; pct: number } | null = null
    for (const [category, pct] of Object.entries(breakdown)) {
      if (!best || pct > best.pct) best = { category, pct }
    }
    return best
  }

  /** "Electricity" reads the scrubbed point's own breakdown -- works at any
   * history/forecast position, not just "now". "Emissions" still needs
   * server-side emission-factor weighting, only computed for the latest
   * observed hour (`current`), so it stays unavailable elsewhere. */
  function breakdownForMix(
    point: HistoryPoint | ForecastPoint | undefined,
    current: CurrentBreakdown | null
  ): Record<string, number> | null {
    if (oko.mixView === 'emissions') return current?.emissions_breakdown_percent || null
    return point?.power_breakdown_percent || current?.power_breakdown_percent || null
  }

  const points = $derived(oko.unifiedPoints(oko.selectedZone))
  const point = $derived(points[oko.horizonIndex])
  const atNow = $derived(oko.horizonIndex === oko.nowSeamIndex(oko.selectedZone))
  const current = $derived(atNow ? oko.lastCurrent : null)
  const shownValue = $derived(oko.pointValue(point))
  const rgb = $derived(rgbForIntensity(shownValue, oko.activeIntensityStops) || [90, 95, 88])

  const breakdown = $derived(breakdownForMix(point, current))
  const dominant = $derived(dominantSource(breakdown))
  const breakdownSub = $derived(oko.mixView === 'emissions' ? '% of gCO2eq emitted' : '% of production')

  const breakdownRows = $derived.by(() => {
    if (!breakdown) return []
    const seen = new Set<string>()
    const rows: string[] = []
    for (const cat of CATEGORY_ORDER) {
      if (breakdown[cat] != null) {
        rows.push(cat)
        seen.add(cat)
      }
    }
    for (const cat of Object.keys(breakdown)) {
      if (!seen.has(cat)) rows.push(cat)
    }
    return rows.map((cat) => ({ cat, pct: breakdown[cat] }))
  })

  const flowEdges = $derived(
    oko.exchangesData.filter((e) => e.zone_from === oko.selectedZone || e.zone_to === oko.selectedZone)
  )
  const flowMaxMagnitude = $derived(flowEdges.reduce((max, e) => Math.max(max, Math.abs(e.net_flow_mw)), 0) || 1)
</script>

<div class="mb-[0.9rem] flex items-baseline justify-between">
  <div>
    <h2 class="mb-[0.15rem] text-[0.95rem]">Current electricity mix</h2>
    <div class="text-[0.75rem] text-muted-foreground">
      {point ? formatWeekdayTime(new Date(point.timestamp), oko.use24h) : 'No observed data yet for this zone.'}
    </div>
  </div>
  <div class="flex items-center gap-[0.35rem]">
    {#if dominant}
      {@const meta = CATEGORY_META[dominant.category]}
      <span class="rounded-full border border-white/18 px-[0.55rem] py-[0.2rem] text-[0.68rem] whitespace-nowrap text-foreground">
        {meta?.icon ?? ''} {Math.round(dominant.pct)}% {dominant.category}
      </span>
    {/if}
    <span class="rounded-full border border-border px-[0.55rem] py-[0.2rem] text-[0.68rem] whitespace-nowrap text-muted-foreground"
      >Preliminary</span
    >
  </div>
</div>

{#if !point}
  <div class="mb-[1.2rem] text-[0.78rem] text-muted-foreground">Waiting on the first production fetch.</div>
{:else}
  <div class="mb-[1.2rem] flex gap-[0.7rem]">
    <div class="flex flex-1 flex-col items-center gap-[0.45rem] text-center">
      <div
        class="flex aspect-square w-full flex-col items-center justify-center gap-[0.1rem] rounded-[14px]"
        style="background: {rgbToHex(rgb)}; color: {readableTextColor(rgb)}"
      >
        <span class="text-[1.35rem] leading-[1.05] font-bold">{shownValue != null ? Math.round(shownValue) : '—'}</span>
        <span class="text-[0.58rem] opacity-85">gCO2eq/kWh</span>
      </div>
      <div class="flex items-center gap-[0.3rem] text-[0.72rem] text-muted-foreground">
        Carbon intensity
        <Tooltip text="Direct-emissions intensity of the most recently observed hour.">
          <span
            class="inline-flex h-[0.95rem] w-[0.95rem] items-center justify-center rounded-full border border-border text-[0.6rem]"
            >i</span
          >
        </Tooltip>
      </div>
    </div>
    <div class="flex flex-1 flex-col items-center gap-[0.45rem] text-center">
      <Gauge percent={current?.fossil_free_percent ?? null} />
      <div class="flex items-center gap-[0.3rem] text-[0.72rem] text-muted-foreground">
        Carbon-free
        <Tooltip text="Renewable-plus-nuclear share of production.">
          <span
            class="inline-flex h-[0.95rem] w-[0.95rem] items-center justify-center rounded-full border border-border text-[0.6rem]"
            >i</span
          >
        </Tooltip>
      </div>
    </div>
    <div class="flex flex-1 flex-col items-center gap-[0.45rem] text-center">
      <Gauge percent={current?.renewable_percent ?? null} />
      <div class="flex items-center gap-[0.3rem] text-[0.72rem] text-muted-foreground">
        Renewable
        <Tooltip text="Renewable share of production.">
          <span
            class="inline-flex h-[0.95rem] w-[0.95rem] items-center justify-center rounded-full border border-border text-[0.6rem]"
            >i</span
          >
        </Tooltip>
      </div>
    </div>
  </div>

  <div class="mt-[1.1rem] mb-[0.55rem] flex items-baseline gap-[0.4rem] border-t border-border pt-[0.9rem] text-[0.76rem] text-muted-foreground">
    <span>⚡ Power breakdown</span> <span class="text-[0.68rem] opacity-80">{breakdownSub}</span>
  </div>
  <div class="mb-[0.6rem] w-fit">
    <Tabs
      bind:value={
        () => oko.mixView,
        (v) => (oko.mixView = v as 'electricity' | 'emissions')
      }
      items={[
        { value: 'electricity', label: 'Electricity' },
        { value: 'emissions', label: 'Emissions' },
      ]}
      size="sm"
    />
  </div>

  {#if !breakdown}
    <div class="mb-[0.4rem] text-[0.78rem] text-muted-foreground">
      {oko.mixView === 'emissions'
        ? 'Emissions breakdown is only available for the latest observed hour — scrub to "now" to see it.'
        : 'No power breakdown available for this hour yet.'}
    </div>
  {:else if !breakdownRows.length}
    <div class="mb-[0.4rem] text-[0.78rem] text-muted-foreground">
      {oko.mixView === 'emissions'
        ? 'No direct emissions this hour (or emissions view not available yet).'
        : 'No breakdown available.'}
    </div>
  {:else}
    <div class="flex flex-col gap-[0.4rem]">
      {#each breakdownRows as { cat, pct } (cat)}
        {@const meta = CATEGORY_META[cat] || { icon: '•', color: '#7a8079' }}
        <div class="flex items-center gap-2 text-[0.78rem]">
          <span class="w-[1.1rem] text-center">{meta.icon}</span>
          <span class="w-[4.6rem] flex-none overflow-hidden text-ellipsis whitespace-nowrap text-muted-foreground capitalize"
            >{cat}</span
          >
          <div class="h-2 flex-1 overflow-hidden rounded-full bg-white/7">
            <div class="h-full rounded-full" style="width:{Math.max(0, Math.min(100, pct))}%;background:{meta.color}"></div>
          </div>
          <span class="w-[2.6rem] flex-none text-right text-muted-foreground tabular-nums">{pct.toFixed(1)}%</span>
        </div>
      {/each}
    </div>
  {/if}
{/if}

<div class="mt-[1.1rem] mb-[0.55rem] flex items-baseline gap-[0.4rem] border-t border-border pt-[0.9rem] text-[0.76rem] text-muted-foreground">
  <span>⇄ Cross-border flows</span> <span class="text-[0.68rem] opacity-80">latest observed hour</span>
</div>
{#if !flowEdges.length}
  <div class="text-[0.78rem] text-muted-foreground">No flow data yet for this zone.</div>
{:else}
  <div class="flex flex-col gap-[0.4rem]">
    {#each flowEdges as edge (edge.zone_from + edge.zone_to)}
      {@const neighbor = edge.zone_from === oko.selectedZone ? edge.zone_to : edge.zone_from}
      {@const importingIntoZone =
        (edge.zone_from === oko.selectedZone && edge.net_flow_mw < 0) ||
        (edge.zone_to === oko.selectedZone && edge.net_flow_mw > 0)}
      {@const magnitude = Math.abs(edge.net_flow_mw)}
      {@const width = Math.max(4, Math.min(100, (magnitude / flowMaxMagnitude) * 100))}
      <div class="flex items-center gap-2 text-[0.78rem]">
        <span class="w-[1.1rem] text-center">{countryFlagEmoji(neighbor)}</span>
        <span class="w-[6rem] flex-none overflow-hidden text-ellipsis whitespace-nowrap text-muted-foreground">{neighbor}</span>
        <div class="h-2 flex-1 overflow-hidden rounded-full bg-white/7">
          <div
            class="h-full rounded-full"
            style="width:{width}%;background:{importingIntoZone ? 'var(--high)' : 'var(--medium)'}"
          ></div>
        </div>
        <span class="w-[5.4rem] flex-none text-right text-muted-foreground tabular-nums"
          >{Math.round(magnitude)} MW {importingIntoZone ? 'importing' : 'exporting'}</span
        >
      </div>
    {/each}
  </div>
{/if}
