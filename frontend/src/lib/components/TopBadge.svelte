<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { formatDate, formatFullDateTime, formatTime } from '$lib/format'

  const point = $derived(oko.unifiedPoints(oko.selectedZone)[oko.horizonIndex])
  const atNow = $derived(oko.horizonIndex === oko.nowSeamIndex(oko.selectedZone))
  const date = $derived(point ? new Date(point.timestamp) : null)
</script>

<div
  class="absolute top-[1.1rem] right-[1.1rem] z-[500] rounded-[10px] border border-border bg-[var(--card-translucent)] px-[0.85rem] py-[0.5rem] text-right text-[0.82rem] leading-[1.35] whitespace-nowrap shadow-[0_8px_24px_rgba(0,0,0,0.35)] backdrop-blur-md"
>
  <div class="font-semibold">{date ? formatDate(date) : '—'}</div>
  <div class="flex items-center justify-end gap-[0.35rem] text-muted-foreground">
    <span>{date ? formatTime(date, oko.use24h) : '—'}</span>
    <span
      class="inline-block h-[0.45rem] w-[0.45rem] rounded-full bg-[var(--high)]"
      class:invisible={!atNow}
      style="box-shadow: 0 0 0 0 rgba(70,196,145,0.6); animation: oko-pulse 2s infinite;"
      title={atNow && date
        ? `Latest observed data as of ${formatFullDateTime(date, oko.use24h)} (ENTSO-E publication lag applies)`
        : ''}
    ></span>
  </div>
</div>

<style>
  @keyframes oko-pulse {
    0% {
      box-shadow: 0 0 0 0 rgba(70, 196, 145, 0.55);
    }
    70% {
      box-shadow: 0 0 0 6px rgba(70, 196, 145, 0);
    }
    100% {
      box-shadow: 0 0 0 0 rgba(70, 196, 145, 0);
    }
  }
</style>
