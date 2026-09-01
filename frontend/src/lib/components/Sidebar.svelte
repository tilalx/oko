<script lang="ts">
  import { oko } from '$lib/state.svelte'
  import { Collapsible } from '$lib/components/ui/collapsible'

  let resourcesOpen = $state(false)
</script>

<aside
  class="flex flex-col gap-[0.15rem] overflow-y-auto border-r border-border bg-[var(--sidebar-bg)] py-4 transition-[flex-basis,width] duration-[180ms] ease-in-out {oko.sidebarCollapsed
    ? 'basis-[60px] w-[60px] px-[0.6rem]'
    : 'basis-[220px] w-[220px] px-[0.85rem]'}"
>
  <div class="flex items-center gap-[0.45rem] overflow-hidden px-2 pt-[0.4rem] pb-4 text-[1.05rem] font-bold whitespace-nowrap">
    ⚡{#if !oko.sidebarCollapsed}<span>OKO</span>{/if}
  </div>

  {#if !oko.sidebarCollapsed}
    <nav class="flex flex-col gap-[0.15rem]">
      <a
        class="flex items-center gap-[0.6rem] rounded-lg px-[0.6rem] py-2 text-[0.87rem] {oko.route === 'docs'
          ? 'text-muted-foreground hover:bg-white/5 hover:text-foreground'
          : 'bg-white/7 text-foreground'}"
        href="#"
      >
        <span class="w-[1.1rem] text-center">🗺️</span><span>Map</span>
      </a>
      <a
        class="flex items-center gap-[0.6rem] rounded-lg px-[0.6rem] py-2 text-[0.87rem] {oko.route === 'docs'
          ? 'bg-white/7 text-foreground'
          : 'text-muted-foreground hover:bg-white/5 hover:text-foreground'}"
        href="#docs"
      >
        <span class="w-[1.1rem] text-center">&lt;/&gt;</span><span>API</span>
      </a>
      <a
        class="flex items-center gap-[0.6rem] rounded-lg px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
        href="https://github.com/tilalx/oko"
        target="_blank"
        rel="noopener"
      >
        <span class="w-[1.1rem] text-center">◍</span><span>GitHub</span>
      </a>
    </nav>

    <div class="mt-[1.1rem] mb-[0.3rem] px-[0.6rem] text-[0.7rem] tracking-wide text-muted-foreground uppercase">
      Reference
    </div>
    <nav class="flex flex-col gap-[0.05rem]">
      <a
        class="flex items-center gap-[0.6rem] rounded-lg px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
        href="#docs"
      >
        <span class="w-[1.1rem] text-center">▤</span><span>API reference</span>
      </a>
      <a
        class="flex items-center gap-[0.6rem] rounded-lg px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
        href="/zones"
        target="_blank"
        rel="noopener"
      >
        <span class="w-[1.1rem] text-center">◔</span><span>Data coverage</span>
      </a>
      <a
        class="flex items-center gap-[0.6rem] rounded-lg px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
        href="https://github.com/tilalx/oko#readme"
        target="_blank"
        rel="noopener"
      >
        <span class="w-[1.1rem] text-center">✎</span><span>Methodology ↗</span>
      </a>
    </nav>

    <Collapsible bind:open={resourcesOpen}>
      {#snippet trigger()}
        <span class="flex items-center gap-2"><span class="w-[1.1rem] text-center">▣</span> Resources</span>
      {/snippet}
      <nav class="flex flex-col gap-[0.05rem] pl-2">
        <a
          class="rounded-lg px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
          href="https://github.com/tilalx/oko/blob/main/ATTRIBUTION.md"
          target="_blank"
          rel="noopener">Attribution</a
        >
        <a
          class="rounded-lg px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
          href="https://github.com/tilalx/oko/blob/main/LICENSE"
          target="_blank"
          rel="noopener">AGPLv3 license</a
        >
      </nav>
    </Collapsible>

    <div class="flex-1"></div>

    {#if !oko.promoDismissed}
      <div class="relative mx-[0.1rem] mt-[0.6rem] mb-[0.55rem] rounded-[10px] border border-border bg-white/[0.045] p-[0.75rem_0.8rem] text-[0.8rem]">
        <button
          class="absolute top-[0.4rem] right-[0.5rem] rounded p-[0.15rem] text-muted-foreground hover:text-foreground"
          aria-label="dismiss"
          onclick={() => oko.dismissPromo()}>×</button
        >
        <strong class="mb-1 block text-[0.85rem]">Run it yourself</strong>
        <p class="leading-[1.4] text-muted-foreground">
          Self-hosted, keyless CO2 forecast. No account, no trial, no API key.
        </p>
      </div>
    {/if}
    <button
      class="mx-[0.1rem] mt-[0.1rem] mb-[0.4rem] rounded-lg bg-[var(--pill-active-bg)] px-[0.7rem] py-[0.55rem] text-[0.85rem] font-semibold text-[var(--pill-active-fg)] hover:opacity-90"
      onclick={() => window.open('https://github.com/tilalx/oko', '_blank', 'noopener')}
    >
      View on GitHub
    </button>
    <a
      class="mx-[0.1rem] mb-[0.6rem] text-center text-[0.8rem] text-muted-foreground hover:text-foreground"
      href="#docs">Explore the API →</a
    >
  {/if}

  <button
    class="self-start rounded-lg border border-border px-[0.5rem] py-[0.35rem] text-[0.8rem] text-muted-foreground hover:border-white/20 hover:text-foreground"
    aria-label="Collapse sidebar"
    onclick={() => oko.setSidebarCollapsed(!oko.sidebarCollapsed)}
  >
    {oko.sidebarCollapsed ? '»' : '«'}
  </button>
</aside>
