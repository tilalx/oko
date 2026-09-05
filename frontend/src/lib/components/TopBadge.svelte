<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { t } from '$lib/i18n'
  import { formatDate, formatFullDateTime, formatTime } from '$lib/format'

  const point = $derived(oko.unifiedPoints(oko.selectedZone)[oko.horizonIndex])
  const atNow = $derived(oko.horizonIndex === oko.nowSeamIndex(oko.selectedZone))
  const date = $derived(point ? new Date(point.timestamp) : null)
</script>

<div
  class="rounded-lg border border-border bg-[var(--card-translucent)] px-[0.85rem] py-[0.5rem] text-right text-[0.82rem] leading-[1.35] whitespace-nowrap backdrop-blur-md"
>
  <div class="oko-num font-semibold">{date ? formatDate(date, oko.locale) : '—'}</div>
  <div class="flex items-center justify-end gap-[0.35rem] text-muted-foreground">
    <span class="oko-num">{date ? formatTime(date, oko.use24h, oko.locale) : '—'}</span>
    <span
      class="inline-block h-[0.45rem] w-[0.45rem] rounded-full bg-[var(--accent-live)]"
      class:invisible={!atNow}
      style="box-shadow: 0 0 0 0 rgba(var(--accent-live-rgb),0.6); animation: oko-pulse 2s infinite;"
      title={atNow && date
        ? t('topBadge.latestDataTitle', { time: formatFullDateTime(date, oko.use24h, oko.locale) })
        : ''}
    ></span>
  </div>
</div>

<style>
  @keyframes oko-pulse {
    0% {
      box-shadow: 0 0 0 0 rgba(var(--accent-live-rgb), 0.55);
    }
    70% {
      box-shadow: 0 0 0 6px rgba(var(--accent-live-rgb), 0);
    }
    100% {
      box-shadow: 0 0 0 0 rgba(var(--accent-live-rgb), 0);
    }
  }
</style>
