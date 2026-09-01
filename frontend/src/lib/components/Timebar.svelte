<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { formatDate, formatTime } from '$lib/format'
  import { Slider } from '$lib/components/ui/slider'

  let playTimer: ReturnType<typeof setInterval> | null = $state(null)

  const points = $derived(oko.unifiedPoints(oko.selectedZone))
  const max = $derived(Math.max(0, points.length - 1))
  const point = $derived(points[oko.horizonIndex])
  const nowSeam = $derived(oko.nowSeamIndex(oko.selectedZone))
  const nowMarkerPct = $derived(max > 0 ? (nowSeam / max) * 100 : 0)

  const dayTicks = $derived.by(() => {
    if (!points.length) return []
    const dayCount = Math.max(1, Math.round(points.length / 24))
    const labels: string[] = []
    for (let d = 0; d < dayCount; d++) {
      const idx = Math.min(d * 24, points.length - 1)
      const date = new Date(points[idx].timestamp)
      labels.push(date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }))
    }
    return labels
  })

  const hint = $derived.by(() => {
    if (!point) return ''
    const offset = oko.horizonIndex - nowSeam
    return offset === 0
      ? 'now'
      : offset > 0
        ? `+${offset}h · ${(point as any).confidence} confidence`
        : `${offset}h · observed`
  })

  function onSliderChange(value: number) {
    oko.horizonIndex = value
    oko.horizonAtNow = value === nowSeam
  }

  function togglePlay() {
    if (playTimer) {
      clearInterval(playTimer)
      playTimer = null
      return
    }
    playTimer = setInterval(() => {
      const next = oko.horizonIndex >= max ? 0 : oko.horizonIndex + 1
      onSliderChange(next)
    }, 300)
  }
</script>

<div class="absolute right-0 bottom-0 left-0 z-[600] border-t border-border bg-[var(--card-translucent)] px-5 pt-[0.6rem] pb-[0.55rem] backdrop-blur-[14px]">
  <div class="mb-[0.35rem] flex items-center gap-[0.9rem]">
    <div class="flex items-baseline gap-2 text-[0.85rem]">
      <span class="font-semibold">{point ? formatDate(new Date(point.timestamp)) : '—'}</span>
      <span class="text-muted-foreground">{point ? formatTime(new Date(point.timestamp), oko.use24h) : '—'}</span>
    </div>
    <button
      class="flex h-7 w-7 items-center justify-center rounded-full border border-border bg-white/5 text-[0.7rem] hover:bg-white/10"
      aria-label="Play through forecast"
      onclick={togglePlay}
    >
      {playTimer ? '⏸' : '▶'}
    </button>
    <div class="flex-1"></div>
    <div class="text-[0.72rem] text-muted-foreground">{hint}</div>
  </div>
  <div class="relative">
    <Slider value={oko.horizonIndex} {max} step={1} onValueChange={onSliderChange} />
    <div
      class="pointer-events-none absolute top-[0.15rem] bottom-[0.15rem] w-[2px] bg-[var(--accent-color)] opacity-85"
      style="left: {nowMarkerPct}%"
    ></div>
  </div>
  <div class="mt-[0.1rem] flex justify-between text-[0.68rem] text-muted-foreground">
    {#each dayTicks as label, i (i)}
      <span>{label}</span>
    {/each}
  </div>
</div>
