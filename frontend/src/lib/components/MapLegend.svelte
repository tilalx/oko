<script lang="ts">
  import { oko } from '$lib/state.svelte'

  const gradient = $derived(
    oko.activeIntensityStops
      .map(([value, rgb]) => `rgb(${rgb.map((c) => Math.round(c)).join(',')}) ${((value / 1500) * 100).toFixed(0)}%`)
      .join(', ')
  )
</script>

<div
  class="absolute right-[1.1rem] bottom-[5.2rem] z-[500] w-[230px] rounded-[10px] border border-border bg-[var(--card-translucent)] p-[0.6rem_0.85rem] text-[0.72rem] text-muted-foreground shadow-[0_8px_24px_rgba(0,0,0,0.35)] backdrop-blur-md"
>
  <div class="mb-[0.4rem] text-[0.74rem] text-foreground">
    {oko.activeLayer === 'lifecycle' ? 'Lifecycle intensity — gCO2eq/kWh' : 'Carbon intensity — gCO2eq/kWh'}
  </div>
  <div class="mb-[0.3rem] h-[0.4rem] rounded-full" style="background: linear-gradient(to right, {gradient})"></div>
  <div class="flex justify-between">
    <span>0</span><span>300</span><span>600</span><span>900</span><span>1200</span><span>1500</span>
  </div>
</div>
