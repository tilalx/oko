<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { Button } from '$lib/components/ui/button'
  import { Popover } from '$lib/components/ui/popover'
  import { Switch } from '$lib/components/ui/switch'

  let settingsOpen = $state(false)
</script>

<div class="absolute top-[4.6rem] right-[1.1rem] z-[500] flex flex-col gap-[0.4rem]">
  <Button
    variant="icon"
    size="icon-sm"
    active={oko.flowLinesVisible}
    title="Toggle cross-border flow lines"
    onclick={() => (oko.flowLinesVisible = !oko.flowLinesVisible)}
  >
    ⇄
  </Button>
  <Button
    variant="icon"
    size="icon-sm"
    title="Toggle basemap shading"
    onclick={() => (oko.tilesLight = !oko.tilesLight)}
  >
    {oko.tilesLight ? '☀' : '☾'}
  </Button>
  <Button
    variant="icon"
    size="icon-sm"
    active={oko.activeLayer === 'lifecycle'}
    title="Toggle direct / lifecycle intensity layer"
    onclick={() => (oko.activeLayer = oko.activeLayer === 'direct' ? 'lifecycle' : 'direct')}
  >
    ▤
  </Button>

  <Popover bind:open={settingsOpen}>
    {#snippet trigger()}
      <Button variant="icon" size="icon-sm" title="Settings">⚙</Button>
    {/snippet}
    <label class="flex cursor-pointer items-center gap-2">
      <Switch checked={oko.colorblindPalette} onCheckedChange={(v) => oko.setColorblind(v)} />
      Colorblind-safe palette
    </label>
    <label class="flex cursor-pointer items-center gap-2">
      <Switch checked={oko.use24h} onCheckedChange={(v) => oko.setUse24h(v)} />
      24-hour clock
    </label>
  </Popover>
</div>
