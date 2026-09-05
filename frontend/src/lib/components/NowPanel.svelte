<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { rgbForIntensity, rgbToHex, readableTextColor } from '$lib/color'
  import { formatWeekdayTime } from '$lib/format'
  import { t, formatNumber } from '$lib/i18n'
  import { CATEGORY_META, CATEGORY_ORDER, FOSSIL_FREE_CATEGORIES, RENEWABLE_CATEGORIES } from '$lib/constants'
  import Gauge from './Gauge.svelte'
  import Icon from './Icon.svelte'
  import { Tabs } from '$lib/components/ui/tabs'
  import { Tooltip } from '$lib/components/ui/tooltip'
  import type { CurrentBreakdown, ForecastPoint, HistoryPoint } from '$lib/api'

  /** Sum of a power breakdown's categories that fall in `categories` --
   * client-side fallback for renewable/fossil-free share at a scrubbed
   * history/forecast point, where the server's precomputed `current`
   * block (only ever for the live "now" hour) isn't available. */
  function shareOf(
    breakdown: Record<string, number> | undefined | null,
    categories: Set<string>
  ): number | null {
    if (!breakdown) return null
    let total = 0
    for (const [category, pct] of Object.entries(breakdown)) {
      if (categories.has(category)) total += pct
    }
    return total
  }

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
    point: HistoryPoint | ForecastPoint | undefined | null,
    current: CurrentBreakdown | null
  ): Record<string, number> | null {
    if (oko.mixView === 'emissions') return current?.emissions_breakdown_percent || null
    return point?.power_breakdown_percent || current?.power_breakdown_percent || null
  }

  const point = $derived(oko.pointAtTime(oko.selectedZone, oko.horizonTime))
  const atNow = $derived(oko.horizonAtNow)
  const current = $derived(atNow ? oko.lastCurrent : null)
  const shownValue = $derived(oko.pointValue(point))
  const rgb = $derived(rgbForIntensity(shownValue, oko.activeIntensityStops) || [90, 95, 88])
  const priceValue = $derived(point?.price_eur_per_mwh ?? null)
  const priceRgb = $derived(rgbForIntensity(priceValue, oko.activePriceStops) || [90, 95, 88])

  const breakdown = $derived(breakdownForMix(point, current))
  const dominant = $derived(dominantSource(breakdown))
  // Always the *electricity* mix (not emissions), regardless of mixView --
  // renewable/carbon-free are production-based shares at any point.
  const powerBreakdown = $derived(point?.power_breakdown_percent || current?.power_breakdown_percent || null)
  const fossilFreePercent = $derived(current?.fossil_free_percent ?? shareOf(powerBreakdown, FOSSIL_FREE_CATEGORIES))
  const renewablePercent = $derived(current?.renewable_percent ?? shareOf(powerBreakdown, RENEWABLE_CATEGORIES))
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
    <h2 class="mb-[0.15rem] text-[0.95rem] font-semibold">{t('nowPanel.electricityMix')}</h2>
    <div class="text-[0.75rem] text-muted-foreground">
      {point ? formatWeekdayTime(new Date(point.timestamp), oko.use24h, oko.locale) : t('nowPanel.noObservedData')}
    </div>
  </div>
  <div class="flex items-center gap-[0.35rem]">
    {#if dominant}
      {@const meta = CATEGORY_META[dominant.category]}
      <span
        class="inline-flex items-center gap-[0.3rem] rounded-full border border-white/18 px-[0.55rem] py-[0.2rem] text-[0.68rem] whitespace-nowrap text-foreground"
      >
        {#if meta}{@html meta.icon}{/if}
        <span class="oko-num">{Math.round(dominant.pct)}%</span> {dominant.category}
      </span>
    {/if}
    <span class="rounded-full border border-border px-[0.55rem] py-[0.2rem] text-[0.68rem] whitespace-nowrap text-muted-foreground"
      >{t('nowPanel.preliminary')}</span
    >
  </div>
</div>

{#if !point}
  <div class="mb-[1.2rem] text-[0.78rem] text-muted-foreground">{t('nowPanel.waitingFirstFetch')}</div>
{:else}
  <div class="mb-[1.2rem] flex gap-[0.7rem]">
    <div class="flex flex-1 flex-col items-center gap-[0.45rem] text-center">
      <div
        class="flex aspect-square w-full flex-col items-center justify-center gap-[0.1rem] rounded-[14px]"
        style="background: {rgbToHex(rgb)}; color: {readableTextColor(rgb)}"
      >
        <span class="oko-num text-[1.5rem] leading-[1.05] font-semibold">{shownValue != null ? Math.round(shownValue) : '—'}</span>
        <span class="text-[0.58rem] opacity-85">gCO2eq/kWh</span>
      </div>
      <div class="flex items-center gap-[0.3rem] text-[0.72rem] text-muted-foreground">
        {t('nowPanel.carbonIntensity')}
        <Tooltip
          text={t('nowPanel.carbonIntensityTooltip')}
        >
          <span
            class="inline-flex h-[0.95rem] w-[0.95rem] items-center justify-center rounded-full border border-border text-[0.6rem]"
            >i</span
          >
        </Tooltip>
      </div>
    </div>
    <div class="flex flex-1 flex-col items-center gap-[0.45rem] text-center">
      <Gauge percent={fossilFreePercent} />
      <div class="flex items-center gap-[0.3rem] text-[0.72rem] text-muted-foreground">
        {t('nowPanel.carbonFree')}
        <Tooltip text={t('nowPanel.carbonFreeTooltip')}>
          <span
            class="inline-flex h-[0.95rem] w-[0.95rem] items-center justify-center rounded-full border border-border text-[0.6rem]"
            >i</span
          >
        </Tooltip>
      </div>
    </div>
    <div class="flex flex-1 flex-col items-center gap-[0.45rem] text-center">
      <Gauge percent={renewablePercent} />
      <div class="flex items-center gap-[0.3rem] text-[0.72rem] text-muted-foreground">
        {t('nowPanel.renewable')}
        <Tooltip text={t('nowPanel.renewableTooltip')}>
          <span
            class="inline-flex h-[0.95rem] w-[0.95rem] items-center justify-center rounded-full border border-border text-[0.6rem]"
            >i</span
          >
        </Tooltip>
      </div>
    </div>
    <div class="flex flex-1 flex-col items-center gap-[0.45rem] text-center">
      <div
        class="flex aspect-square w-full flex-col items-center justify-center gap-[0.1rem] rounded-[14px]"
        style="background: {rgbToHex(priceRgb)}; color: {readableTextColor(priceRgb)}"
      >
        <span class="oko-num text-[1.5rem] leading-[1.05] font-semibold">{priceValue != null ? Math.round(priceValue) : '—'}</span>
        <span class="text-[0.58rem] opacity-85">EUR/MWh</span>
      </div>
      <div class="flex items-center gap-[0.3rem] text-[0.72rem] text-muted-foreground">
        {t('nowPanel.price')}
        <Tooltip text={t('nowPanel.priceTooltip')}>
          <span
            class="inline-flex h-[0.95rem] w-[0.95rem] items-center justify-center rounded-full border border-border text-[0.6rem]"
            >i</span
          >
        </Tooltip>
      </div>
    </div>
  </div>

  <div class="mt-[1.1rem] mb-[0.55rem] flex items-center gap-[0.4rem] border-t border-border pt-[0.9rem] text-[0.76rem] text-muted-foreground">
    <Icon name="gauge" size="0.95em" />
    <span class="text-foreground">{t('nowPanel.powerBreakdown')}</span>
    <span class="text-[0.68rem] opacity-80">{breakdownSub}</span>
  </div>
  <div class="mb-[0.6rem] w-fit">
    <Tabs
      bind:value={
        () => oko.mixView,
        (v) => (oko.mixView = v as 'electricity' | 'emissions')
      }
      items={[
        { value: 'electricity', label: t('nowPanel.tabElectricity') },
        { value: 'emissions', label: t('nowPanel.tabEmissions') },
      ]}
      size="sm"
    />
  </div>

  {#if !breakdown}
    <div class="mb-[0.4rem] text-[0.78rem] text-muted-foreground">
      {oko.mixView === 'emissions'
        ? t('nowPanel.breakdownUnavailable')
        : t('nowPanel.noBreakdownAvailable')}
    </div>
  {:else if !breakdownRows.length}
    <div class="mb-[0.4rem] text-[0.78rem] text-muted-foreground">
      {oko.mixView === 'emissions'
        ? t('nowPanel.noBreakdownAvailable')
        : t('nowPanel.noBreakdownAvailable')}
    </div>
  {:else}
    <div class="flex flex-col gap-[0.4rem]">
      {#each breakdownRows as { cat, pct } (cat)}
        {@const meta = CATEGORY_META[cat]}
        <div class="flex items-center gap-2 text-[0.78rem]">
          <span class="flex w-[1.1rem] items-center justify-center">{#if meta}{@html meta.icon}{/if}</span>
          <span class="w-[4.6rem] flex-none overflow-hidden text-ellipsis whitespace-nowrap text-muted-foreground capitalize"
            >{cat}</span
          >
          <div class="h-[5px] flex-1 overflow-hidden rounded-full bg-white/7">
            <div
              class="h-full rounded-full"
              style="width:{Math.max(0, Math.min(100, pct))}%;background:{meta?.color ?? '#7a8079'}"
            ></div>
          </div>
          <span class="oko-num w-[2.6rem] flex-none text-right text-muted-foreground">{formatNumber(pct, { locale: oko.locale, minimumFractionDigits: 1, maximumFractionDigits: 1 })}%</span>
        </div>
      {/each}
    </div>
  {/if}
{/if}

<div class="mt-[1.1rem] mb-[0.55rem] flex items-center gap-[0.4rem] border-t border-border pt-[0.9rem] text-[0.76rem] text-muted-foreground">
  <Icon name="flow" size="0.95em" />
  <span class="text-foreground">{t('nowPanel.crossBorderFlows')}</span>
  <span class="text-[0.68rem] opacity-80">{t('nowPanel.latestObservedHour')}</span>
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
        <span class="w-[4.6rem] flex-none overflow-hidden text-ellipsis whitespace-nowrap text-muted-foreground">{neighbor}</span>
        <div class="h-[5px] flex-1 overflow-hidden rounded-full bg-white/7">
          <div
            class="h-full rounded-full"
            style="width:{width}%;background:{importingIntoZone ? 'var(--high)' : 'var(--medium)'}"
          ></div>
        </div>
        <span class="oko-num w-[5.4rem] flex-none text-right text-muted-foreground"
          >{formatNumber(Math.round(magnitude), { locale: oko.locale })} MW {importingIntoZone ? t('nowPanel.importing') : t('nowPanel.exporting')}</span
        >
      </div>
    {/each}
  </div>
{/if}
