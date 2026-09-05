<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { t } from '$lib/i18n'
  import { Card } from '$lib/components/ui/card'
  import { Tabs } from '$lib/components/ui/tabs'
  import { Select } from '$lib/components/ui/select'
  import NowPanel from './NowPanel.svelte'
  import ForecastPanel from './ForecastPanel.svelte'
  import Icon from './Icon.svelte'
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
        class="flex items-center justify-center rounded-md p-[0.3rem] text-muted-foreground hover:bg-white/6 hover:text-foreground"
        title={t('zoneCard.hidePanel')}
        onclick={() => (oko.cardVisible = false)}><Icon name="back" size="1.05em" /></button
      >
      <span
        class="oko-num flex-none rounded border border-border bg-white/5 px-[0.35rem] py-[0.1rem] text-[0.68rem] font-semibold text-muted-foreground"
        >{oko.selectedZone.split('-')[0]}</span
      >
      <span class="flex-1 overflow-hidden text-[1.02rem] font-semibold text-ellipsis whitespace-nowrap"
        >{t(`zones.${oko.selectedZone}`) || oko.selectedZone}</span
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
          { value: 'now', label: t('zoneCard.tabNow') },
          { value: 'forecast', label: t('zoneCard.tabForecast') },
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
      {#if payload}
        <footer class="mt-[0.5rem] border-t border-border pt-[0.6rem] text-[0.7rem] leading-[1.6] text-muted-foreground">
          <div class="mb-[0.15rem] text-muted-foreground/70">{t('zoneCard.dataSources')}</div>
          {#each payload.attribution || [] as source (source)}
            <div>{source}</div>
          {/each}
          <a
            class="mt-[0.2rem] inline-block underline"
            href={payload.source || 'https://github.com/tilalx/oko'}
            target="_blank"
            rel="noopener">{t('zoneCard.viewSourceCode')}</a
          >
        </footer>
      {/if}
    </div>
  </Card>
{/if}
