<script lang="ts">
  let { percent }: { percent: number | null } = $props()

  const r = 26
  const c = 2 * Math.PI * r
  const pct = $derived(percent == null ? 0 : Math.max(0, Math.min(100, percent)))
  const dash = $derived((pct / 100) * c)
</script>

<div class="relative aspect-square w-full">
  <svg viewBox="0 0 64 64" class="h-full w-full">
    <circle cx="32" cy="32" {r} fill="none" stroke="rgba(255,255,255,0.12)" stroke-width="7" />
    <circle
      cx="32"
      cy="32"
      {r}
      fill="none"
      stroke="var(--high)"
      stroke-width="7"
      stroke-linecap="round"
      stroke-dasharray="{dash} {c}"
      transform="rotate(-90 32 32)"
      class="transition-[stroke-dasharray] duration-400 ease-out"
    />
  </svg>
  <div class="oko-num absolute inset-0 flex items-center justify-center text-[1.15rem] font-semibold">
    {percent == null ? '—' : Math.round(percent) + '%'}
  </div>
</div>
