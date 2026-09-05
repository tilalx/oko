<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { t } from '$lib/i18n'
  import { Button } from '$lib/components/ui/button'
  import { Popover } from '$lib/components/ui/popover'
  import { Switch } from '$lib/components/ui/switch'
  import Icon from './Icon.svelte'
  import type MapView from './MapView.svelte'

  let { mapView }: { mapView: MapView | undefined } = $props()

  let settingsOpen = $state(false)
</script>

<div class="flex flex-col gap-[0.4rem]">
  <div class="flex flex-col overflow-hidden rounded-lg border border-border bg-[var(--card-translucent)] backdrop-blur-md">
    <button
      class="flex h-[34px] w-[34px] items-center justify-center text-foreground hover:bg-white/6"
      title={t('mapActions.zoomIn')}
      onclick={() => mapView?.zoomIn()}
    >
      <Icon name="zoom-in" size="1.05em" />
    </button>
    <div class="border-t border-border"></div>
    <button
      class="flex h-[34px] w-[34px] items-center justify-center text-foreground hover:bg-white/6"
      title={t('mapActions.zoomOut')}
      onclick={() => mapView?.zoomOut()}
    >
      <Icon name="zoom-out" size="1.05em" />
    </button>
  </div>
  <Button
    variant="icon"
    size="icon-sm"
    active={oko.flowLinesVisible}
    title={t('mapActions.toggleFlows')}
    onclick={() => (oko.flowLinesVisible = !oko.flowLinesVisible)}
  >
    <Icon name="flow" size="1.05em" />
  </Button>
  <Popover bind:open={settingsOpen}>
    {#snippet trigger()}
      <Button variant="icon" size="icon-sm" title={t('mapActions.settings')}><Icon name="settings" size="1.05em" /></Button>
    {/snippet}
    <label class="flex cursor-pointer items-center gap-2">
      <Switch checked={oko.colorblindPalette} onCheckedChange={(v) => oko.setColorblind(v)} />
      {t('mapActions.colorblindPalette')}
    </label>
    <label class="flex cursor-pointer items-center gap-2">
      <Switch checked={oko.use24h} onCheckedChange={(v) => oko.setUse24h(v)} />
      {t('mapActions.use24hClock')}
    </label>
  </Popover>
</div>
