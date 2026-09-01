<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { countryFlagEmoji } from '$lib/format'
  import { ZONE_NAMES } from '$lib/constants'
  import { Card } from '$lib/components/ui/card'
  import { Tabs } from '$lib/components/ui/tabs'
  import { Select } from '$lib/components/ui/select'
  import NowPanel from './NowPanel.svelte'
  import ForecastPanel from './ForecastPanel.svelte'
  import type { ForecastPayload } from '$lib/api'

  let {
    status,
    payload,
    onSelectZone,
  }: {
    status: string
    payload: ForecastPayload | null
    onSelectZone: (zone: string) => void
  } = $props()

  let activeTab = $state<'now' | 'forecast'>('now')
</script>

{#if oko.cardVisible}
  <Card class="absolute top-[1.1rem] left-[1.1rem] z-[550] w-[380px] max-w-[calc(100%-2.2rem)] max-h-[calc(100%-7.8rem)]">
    <div class="flex items-center gap-[0.6rem] px-4 pt-[0.9rem] pb-[0.7rem]">
      <button
        class="rounded-md p-[0.15rem_0.3rem] text-muted-foreground hover:bg-white/6 hover:text-foreground"
        title="Hide panel"
        onclick={() => (oko.cardVisible = false)}>←</button
      >
      <span class="text-[1.3rem] leading-none">{countryFlagEmoji(oko.selectedZone)}</span>
      <span class="flex-1 overflow-hidden text-[1.02rem] font-bold text-ellipsis whitespace-nowrap"
        >{ZONE_NAMES[oko.selectedZone] || oko.selectedZone}</span
      >
      <Select
        value={oko.selectedZone}
        items={oko.allZones.map((z) => ({ value: z, label: z }))}
        onValueChange={(v: string) => v && onSelectZone(v)}
      />
    </div>

    <div class="mx-4 mb-[0.9rem] w-fit">
      <Tabs
        bind:value={
          () => activeTab,
          (v) => (activeTab = v as 'now' | 'forecast')
        }
        items={[
          { value: 'now', label: 'Now' },
          { value: 'forecast', label: 'Forecast' },
        ]}
      />
    </div>

    <div class="overflow-y-auto px-4 pb-[1.1rem] [scrollbar-width:thin]">
      {#if activeTab === 'now'}
        <NowPanel />
      {:else}
        <ForecastPanel {payload} />
      {/if}

      <div class="mt-[0.2rem] text-[0.78rem] text-muted-foreground">{status}</div>
      <footer class="mt-[0.5rem] border-t border-border pt-[0.7rem] text-[0.7rem] leading-[1.5] text-muted-foreground">
        {#if payload}
          Data: {(payload.attribution || []).join(' · ')} —
          <a
            class="underline"
            href={payload.source || 'https://github.com/tilalx/oko'}
            target="_blank"
            rel="noopener">source</a
          >
        {/if}
      </footer>
    </div>
  </Card>
{/if}
