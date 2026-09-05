<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { t } from '$lib/i18n'
  import { formatDate, formatTime } from '$lib/format'
  import { Slider } from '$lib/components/ui/slider'
  import Icon from './Icon.svelte'

  const HOUR_MS = 3_600_000

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

  /** Ruler grid per granularity -- a fixed, readable scale rather than a
   * step search, so a given zoom level always means the same thing:
   *   day   -- one line per hour, labeled with the hour
   *   week  -- one labeled date line per day, plus a line every 6 hours
   *            carrying the hour
   *   month -- one line per day, labeled with the date
   * Ticks walk local wall-clock (setHours/setDate), not fixed millisecond
   * multiples, so they land on local midnight and survive DST shifts. */
  const HOUR_STEP: Record<'day' | 'week' | 'month', number | null> = {
    day: 1,
    week: 6,
    month: null,
  }

  /** Measured tick-strip width -- label density is decided in pixels, so a
   * phone-width bar thins its labels instead of overprinting them. */
  let barWidth = $state(0)

  const pct = (ms: number) => ((ms - windowStart) / (windowEnd - windowStart)) * 100

  /** Local wall-clock ticks every `stepHours`, aligned to local midnight. */
  function hourGrid(stepHours: number): number[] {
    const out: number[] = []
    if (windowEnd <= windowStart) return out
    const d = new Date(windowStart)
    d.setMinutes(0, 0, 0)
    d.setHours(Math.floor(d.getHours() / stepHours) * stepHours)
    while (d.getTime() <= windowEnd) {
      if (d.getTime() >= windowStart) out.push(d.getTime())
      d.setHours(d.getHours() + stepHours)
    }
    return out
  }

  /** Local midnights inside the window. */
  const dayGrid = $derived.by(() => {
    const out: number[] = []
    if (windowEnd <= windowStart) return out
    const d = new Date(windowStart)
    d.setHours(0, 0, 0, 0)
    while (d.getTime() <= windowEnd) {
      if (d.getTime() >= windowStart) out.push(d.getTime())
      d.setDate(d.getDate() + 1)
    }
    return out
  })

  const hourGridTicks = $derived.by(() => {
    const step = HOUR_STEP[oko.windowGranularity]
    return step ? hourGrid(step) : []
  })

  /** Keep every nth label so no label gets less than `minPx` of bar. */
  function stride(count: number, minPx: number): number {
    if (count < 2 || barWidth <= 0) return 1
    return Math.max(1, Math.ceil(minPx / (barWidth / (count - 1))))
  }

  const DATE_LABEL_PX = 54
  const HOUR_LABEL_PX = 30

  const dayTicks = $derived.by(() => {
    const step = stride(dayGrid.length, DATE_LABEL_PX)
    return dayGrid.map((ms, i) => ({
      ms,
      pct: pct(ms),
      label: i % step === 0 ? new Date(ms).toLocaleDateString(oko.locale, { month: 'short', day: 'numeric' }) : '',
    }))
  })

  /** Hour lines. A tick landing on midnight is already drawn and labeled as
   * a day line, and an hour label too close to a date label would overprint
   * it -- both drop their hour label. */
  const hourTicks = $derived.by(() => {
    const step = stride(hourGridTicks.length, HOUR_LABEL_PX)
    const labelledDays = dayTicks.filter((t) => t.label).map((t) => t.ms)
    const clearanceMs =
      barWidth > 0 ? ((windowEnd - windowStart) * ((DATE_LABEL_PX + HOUR_LABEL_PX) / 2)) / barWidth : 0
    return hourGridTicks.map((ms, i) => {
      const d = new Date(ms)
      const crowded = labelledDays.some((day) => Math.abs(day - ms) < clearanceMs)
      return {
        ms,
        pct: pct(ms),
        label: crowded || i % step !== 0 ? '' : formatTime(d, oko.use24h, oko.locale),
      }
    })
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

<div class="absolute right-0 bottom-0 left-0 z-[600] border-t border-border bg-[var(--card-translucent)] px-3 pt-[0.6rem] pb-[0.55rem] backdrop-blur-[14px] sm:px-5">
  <div class="mb-[0.35rem] flex flex-wrap items-center gap-x-[0.7rem] gap-y-[0.4rem] sm:gap-x-[0.9rem]">
    <div class="oko-num flex items-baseline gap-2 text-[0.85rem]">
      <span class="font-semibold">{point ? formatDate(new Date(point.timestamp), oko.locale) : '—'}</span>
      <span class="text-muted-foreground">{point ? formatTime(new Date(point.timestamp), oko.use24h, oko.locale) : '—'}</span>
    </div>
    <button
      class="flex h-8 w-8 items-center justify-center rounded-full border border-border bg-white/5 text-[0.7rem] hover:bg-white/10 sm:h-7 sm:w-7"
      aria-label={t('timebar.playLabel')}
      onclick={togglePlay}
    >
      <Icon name={playTimer ? 'pause' : 'play'} size="0.8em" />
    </button>
    <button
      class="flex h-8 w-8 items-center justify-center rounded-full border border-border bg-white/5 text-[0.7rem] hover:bg-white/10 disabled:opacity-40 sm:h-7 sm:w-7"
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
          class="px-[0.7rem] py-[0.3rem] sm:px-[0.55rem] sm:py-[0.2rem] {oko.windowGranularity === key
            ? 'bg-[var(--accent-color)] text-black'
            : 'bg-white/5 hover:bg-white/10'}"
          onclick={() => (oko.windowGranularity = key as 'day' | 'week' | 'month')}
        >
          {label}
        </button>
      {/each}
    </div>
    <div class="flex-1"></div>
    <div class="oko-num hidden text-[0.72rem] text-muted-foreground sm:block">{hint}</div>
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
  <div class="relative h-[6px]" bind:clientWidth={barWidth}>
    {#each hourTicks as tick (tick.ms)}
      <span class="absolute top-0 h-[4px] w-px bg-white/12" style="left: {tick.pct}%"></span>
    {/each}
    {#each dayTicks as tick (tick.ms)}
      <span class="absolute top-0 h-full w-px bg-white/30" style="left: {tick.pct}%"></span>
    {/each}
  </div>
  <div class="oko-num relative mt-[0.15rem] h-[1.1rem] text-[0.68rem]">
    {#each hourTicks as tick (tick.ms)}
      {#if tick.label}
        <span class="absolute -translate-x-1/2 text-muted-foreground/60" style="left: {tick.pct}%">{tick.label}</span>
      {/if}
    {/each}
    {#each dayTicks as tick (tick.ms)}
      {#if tick.label}
        <span class="absolute -translate-x-1/2 font-semibold text-muted-foreground" style="left: {tick.pct}%">{tick.label}</span>
      {/if}
    {/each}
  </div>
</div>
