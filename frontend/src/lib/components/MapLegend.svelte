<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { t } from '$lib/i18n'

  const gradient = $derived(
    oko.activeIntensityStops
      .map(([value, rgb]) => `rgb(${rgb.map((c) => Math.round(c)).join(',')}) ${((value / 1500) * 100).toFixed(0)}%`)
      .join(', ')
  )
</script>

<div
  class="absolute right-[1.1rem] bottom-[5.2rem] z-[500] w-[230px] rounded-lg border border-border bg-[var(--card-translucent)] p-[0.6rem_0.85rem] text-[0.72rem] text-muted-foreground backdrop-blur-md"
>
  <div class="mb-[0.4rem] text-[0.74rem] text-foreground">{t('mapLegend.carbonIntensity')}</div>
  <div class="mb-[0.3rem] h-[5px] rounded-full" style="background: linear-gradient(to right, {gradient})"></div>
  <div class="oko-num flex justify-between">
    <span>0</span><span>300</span><span>600</span><span>900</span><span>1200</span><span>1500</span>
  </div>
</div>
