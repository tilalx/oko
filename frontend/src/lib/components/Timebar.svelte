<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { t } from '$lib/i18n'
  import { formatDate, formatTime } from '$lib/format'
  import { Slider } from '$lib/components/ui/slider'
  import Icon from './Icon.svelte'

  const HOUR_MS = 3_600_000
  const DAY_MS = 24 * HOUR_MS

  let playTimer: ReturnType<typeof setInterval> | null = $state(null)

  const windowStart = $derived(oko.windowStartMs)
  const windowEnd = $derived(oko.windowEndMs)
  const windowHours = $derived(Math.round((windowEnd - windowStart) / HOUR_MS))
  const point = $derived(oko.pointAtTime(oko.selectedZone, oko.horizonTime))
  /** "now" always sits at the same fixed ratio of the bar, regardless of
   * zone -- windowStart/windowEnd are both anchored to nowHourMs, so this
   * reduces to a constant per granularity. */
  const nowMarkerPct = $derived(windowHours > 0 ? ((oko.nowHourMs - windowStart) / (windowEnd - windowStart)) * 100 : 0)
  const sliderValue = $derived(Math.round((oko.horizonTime - windowStart) / HOUR_MS))

  /** Slider drag/step granularity -- fine (hourly) for "day"/"week" where an
   * hour is a meaningful jump, coarse (daily) for "month" where hour steps
   * would need 720 increments to cross the window. */
  const sliderStep = $derived(oko.windowGranularity === 'month' ? 24 : 1)

  /** Ruler tick marks along the bar: a "nice" step is picked so roughly 6
   * labeled (major) ticks fit the current window, each subdivided into 6
   * unlabeled (minor) ticks for the ruler look. A major tick reads as a
   * date when it lands on midnight, otherwise as a time -- so "day"/"week"
   * naturally alternate date/time labels while "month" (whose steps are
   * always whole days) shows dates only. */
  const MAJOR_STEPS_MS = [HOUR_MS, 2 * HOUR_MS, 3 * HOUR_MS, 6 * HOUR_MS, 12 * HOUR_MS, DAY_MS, 2 * DAY_MS, 5 * DAY_MS, 10 * DAY_MS, 30 * DAY_MS]
  const TARGET_MAJOR_TICKS = 6

  const majorStepMs = $derived.by(() => {
    const total = windowEnd - windowStart
    if (total <= 0) return HOUR_MS
    for (const step of MAJOR_STEPS_MS) {
      if (total / step <= TARGET_MAJOR_TICKS) return step
    }
    return MAJOR_STEPS_MS[MAJOR_STEPS_MS.length - 1]
  })

  const majorTicks = $derived.by(() => {
    const ticks: { label: string; pct: number }[] = []
    if (windowEnd <= windowStart) return ticks
    const step = majorStepMs
    const first = Math.ceil(windowStart / step) * step
    for (let ms = first; ms <= windowEnd; ms += step) {
      const pct = ((ms - windowStart) / (windowEnd - windowStart)) * 100
      const d = new Date(ms)
      const label =
        d.getHours() === 0 && d.getMinutes() === 0
          ? d.toLocaleDateString(oko.locale, { month: 'short', day: 'numeric' })
          : formatTime(d, oko.use24h, oko.locale)
      ticks.push({ label, pct })
    }
    return ticks
  })

  const minorTicks = $derived.by(() => {
    if (windowEnd <= windowStart || majorStepMs <= HOUR_MS) return []
    const step = majorStepMs / 6
    const first = Math.ceil(windowStart / step) * step
    const pcts: number[] = []
    for (let ms = first; ms <= windowEnd; ms += step) {
      pcts.push(((ms - windowStart) / (windowEnd - windowStart)) * 100)
    }
    return pcts
  })

  const hint = $derived.by(() => {
    if (!point) return ''
    const offsetHours = Math.round((oko.horizonTime - oko.nowHourMs) / HOUR_MS)
    return offsetHours === 0
      ? t('timebar.nowLabel')
      : offsetHours > 0
        ? t('timebar.offsetConfidence', { offset: offsetHours, confidence: (point as any).confidence })
        : t('timebar.offsetObserved', { offset: offsetHours })
  })

  function onSliderChange(value: number) {
    const time = windowStart + value * HOUR_MS
    oko.horizonTime = time
    oko.horizonAtNow = time === oko.nowHourMs
  }

  function jumpToNow() {
    oko.horizonTime = oko.nowHourMs
    oko.horizonAtNow = true
  }

  function togglePlay() {
    if (playTimer) {
      clearInterval(playTimer)
      playTimer = null
      return
    }
    playTimer = setInterval(() => {
      const next = oko.horizonTime + HOUR_MS
      onSliderChange(Math.round(((next > windowEnd ? windowStart : next) - windowStart) / HOUR_MS))
    }, 300)
  }
</script>

<div class="absolute right-0 bottom-0 left-0 z-[600] border-t border-border bg-[var(--card-translucent)] px-5 pt-[0.6rem] pb-[0.55rem] backdrop-blur-[14px]">
  <div class="mb-[0.35rem] flex items-center gap-[0.9rem]">
    <div class="oko-num flex items-baseline gap-2 text-[0.85rem]">
      <span class="font-semibold">{point ? formatDate(new Date(point.timestamp), oko.locale) : '—'}</span>
      <span class="text-muted-foreground">{point ? formatTime(new Date(point.timestamp), oko.use24h, oko.locale) : '—'}</span>
    </div>
    <button
      class="flex h-7 w-7 items-center justify-center rounded-full border border-border bg-white/5 text-[0.7rem] hover:bg-white/10"
      aria-label={t('timebar.playLabel')}
      onclick={togglePlay}
    >
      <Icon name={playTimer ? 'pause' : 'play'} size="0.8em" />
    </button>
    <button
      class="flex h-7 w-7 items-center justify-center rounded-full border border-border bg-white/5 text-[0.7rem] hover:bg-white/10 disabled:opacity-40"
      aria-label={t('timebar.nowButtonLabel')}
      title={t('timebar.nowButtonLabel')}
      disabled={oko.horizonAtNow}
      onclick={jumpToNow}
    >
      <Icon name="target" size="0.8em" />
    </button>
    <div class="flex overflow-hidden rounded-full border border-border text-[0.68rem]">
      {#each [
        ['day', t('timebar.granularityDay')],
        ['week', t('timebar.granularityWeek')],
        ['month', t('timebar.granularityMonth')],
      ] as [key, label] (key)}
        <button
          class="px-[0.55rem] py-[0.2rem] {oko.windowGranularity === key
            ? 'bg-[var(--accent-color)] text-black'
            : 'bg-white/5 hover:bg-white/10'}"
          onclick={() => (oko.windowGranularity = key as 'day' | 'week' | 'month')}
        >
          {label}
        </button>
      {/each}
    </div>
    <div class="flex-1"></div>
    <div class="oko-num text-[0.72rem] text-muted-foreground">{hint}</div>
  </div>
  <div class="relative py-[0.4rem]">
    {#key oko.windowGranularity}
      <Slider value={sliderValue} max={windowHours} step={sliderStep} onValueChange={onSliderChange} />
    {/key}
    <div
      class="pointer-events-none absolute top-0 bottom-0 w-[3px] -translate-x-1/2 rounded-full bg-[var(--accent-live)]"
      style="left: {nowMarkerPct}%; box-shadow: 0 0 6px 0 rgba(var(--accent-live-rgb), 0.7)"
    ></div>
  </div>
  <div class="relative h-[6px]">
    {#each minorTicks as pct (pct)}
      <span class="absolute top-0 h-full w-px bg-white/12" style="left: {pct}%"></span>
    {/each}
    {#each majorTicks as tick (tick.pct)}
      <span class="absolute top-0 h-full w-px bg-white/30" style="left: {tick.pct}%"></span>
    {/each}
  </div>
  <div class="oko-num relative mt-[0.15rem] h-[1.1rem] text-[0.68rem] text-muted-foreground">
    {#each majorTicks as tick (tick.pct)}
      <span class="absolute -translate-x-1/2" style="left: {tick.pct}%">{tick.label}</span>
    {/each}
  </div>
</div>
