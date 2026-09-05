<script lang="ts">
  import { onMount } from 'svelte'
  import { oko } from '$lib/state.svelte'
  import { t } from '$lib/i18n'
  import { Collapsible } from '$lib/components/ui/collapsible'
  import Icon from './Icon.svelte'

  let resourcesOpen = $state(false)

  /** Phone-sized viewport: the sidebar is a full off-canvas drawer over the
   * map rather than a column beside it, so "collapsed" means hidden, not
   * narrowed -- and its content stays mounted while it slides out. */
  let narrow = $state(false)
  onMount(() => {
    const mq = window.matchMedia('(max-width: 639px)')
    const sync = () => (narrow = mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  })

  const showContent = $derived(narrow || !oko.sidebarCollapsed)
</script>

{#if narrow && oko.sidebarCollapsed}
  <button
    class="fixed top-2 left-2 z-[800] flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-[var(--card-translucent)] text-foreground backdrop-blur-md"
    aria-label={t('sidebar.openLabel')}
    onclick={() => oko.setSidebarCollapsed(false)}
  >
    <Icon name="menu" size="1.1em" />
  </button>
{/if}

{#if narrow && !oko.sidebarCollapsed}
  <button
    class="fixed inset-0 z-[850] bg-black/50"
    aria-label={t('sidebar.collapseLabel')}
    onclick={() => oko.setSidebarCollapsed(true)}
  ></button>
{/if}

<!-- Off-canvas but still in the DOM on phones (so it can slide) -- inert
     keeps it out of the tab order and the accessibility tree while hidden. -->
<aside
  inert={narrow && oko.sidebarCollapsed}
  class="fixed inset-y-0 left-0 z-[900] flex w-[240px] flex-col gap-[0.15rem] overflow-y-auto border-r border-border bg-[var(--sidebar-bg)] px-[0.85rem] py-4 transition-transform duration-[180ms] ease-in-out sm:static sm:z-auto sm:translate-x-0 sm:transition-[flex-basis,width] {oko.sidebarCollapsed
    ? '-translate-x-full sm:basis-[60px] sm:w-[60px] sm:px-[0.6rem]'
    : 'translate-x-0 sm:basis-[220px] sm:w-[220px] sm:px-[0.85rem]'}"
>
  <div class="mb-[0.3rem] flex items-center gap-[0.55rem] overflow-hidden px-2 pt-[0.4rem] pb-4 whitespace-nowrap">
    <span class="inline-block h-[0.55rem] w-[0.55rem] flex-none rounded-full bg-[var(--accent-color)]"></span>
    {#if showContent}<span class="oko-num text-[1.05rem] font-semibold tracking-[0.02em]">OKO</span>{/if}
  </div>

  {#if showContent}
    <nav class="flex flex-col gap-[0.15rem]">
      <a
        class="flex items-center gap-[0.65rem] rounded-md px-[0.6rem] py-2 text-[0.87rem] {oko.route === 'docs'
          ? 'text-muted-foreground hover:bg-white/5 hover:text-foreground'
          : 'bg-white/7 text-foreground'}"
        href="#"
      >
        <Icon name="map" size="1.05em" /><span>{t('sidebar.mapLabel')}</span>
      </a>
      <a
        class="flex items-center gap-[0.65rem] rounded-md px-[0.6rem] py-2 text-[0.87rem] {oko.route === 'docs'
          ? 'bg-white/7 text-foreground'
          : 'text-muted-foreground hover:bg-white/5 hover:text-foreground'}"
        href="#docs"
      >
        <Icon name="code" size="1.05em" /><span>{t('sidebar.apiLabel')}</span>
      </a>
      <a
        class="flex items-center gap-[0.65rem] rounded-md px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
        href="https://github.com/tilalx/oko"
        target="_blank"
        rel="noopener"
      >
        <Icon name="github" size="1.05em" /><span>{t('sidebar.githubLabel')}</span>
      </a>
    </nav>

    <div class="mt-[1.1rem] mb-[0.3rem] px-[0.6rem] text-[0.7rem] text-muted-foreground/70">{t('sidebar.referenceLabel')}</div>
    <nav class="flex flex-col gap-[0.05rem]">
      <a
        class="flex items-center gap-[0.65rem] rounded-md px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
        href="#docs"
      >
        <Icon name="docs" size="1.05em" /><span>{t('sidebar.apiReference')}</span>
      </a>
      <a
        class="flex items-center gap-[0.65rem] rounded-md px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
        href="/zones"
        target="_blank"
        rel="noopener"
      >
        <Icon name="coverage" size="1.05em" /><span>{t('sidebar.dataCoverage')}</span>
      </a>
      <a
        class="flex items-center gap-[0.65rem] rounded-md px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
        href="https://github.com/tilalx/oko#readme"
        target="_blank"
        rel="noopener"
      >
        <Icon name="external" size="1.05em" /><span>{t('sidebar.methodology')}</span>
      </a>
    </nav>

    <Collapsible bind:open={resourcesOpen}>
      {#snippet trigger()}
        <span class="flex items-center gap-[0.65rem]"><Icon name="layers" size="1.05em" /> {t('sidebar.resources')}</span>
      {/snippet}
      <nav class="flex flex-col gap-[0.05rem] pl-2">
        <a
          class="rounded-md px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
          href="https://github.com/tilalx/oko/blob/main/ATTRIBUTION.md"
          target="_blank"
          rel="noopener">{t('sidebar.attribution')}</a
        >
        <a
          class="rounded-md px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
          href="https://github.com/tilalx/oko/blob/main/LICENSE"
          target="_blank"
          rel="noopener">{t('sidebar.license')}</a
        >
      </nav>
    </Collapsible>

    <div class="flex-1"></div>

    {#if !oko.promoDismissed}
      <div class="relative mx-[0.1rem] mt-[0.6rem] mb-[0.55rem] rounded-md border border-border bg-white/[0.045] p-[0.75rem_0.8rem] text-[0.8rem]">
        <button
          class="absolute top-[0.4rem] right-[0.5rem] rounded p-[0.15rem] text-muted-foreground hover:text-foreground"
          aria-label={t('sidebar.dismissLabel')}
          onclick={() => oko.dismissPromo()}><Icon name="close" size="0.8em" /></button
        >
        <strong class="mb-1 block text-[0.85rem] font-semibold">{t('sidebar.runYourself')}</strong>
        <p class="leading-[1.4] text-muted-foreground">
          {t('sidebar.runYourselfDesc')}
        </p>
      </div>
    {/if}
    <button
      class="mx-[0.1rem] mt-[0.1rem] mb-[0.4rem] rounded-md bg-[var(--pill-active-bg)] px-[0.7rem] py-[0.55rem] text-[0.85rem] font-semibold text-[var(--pill-active-fg)] hover:opacity-90"
      onclick={() => window.open('https://github.com/tilalx/oko', '_blank', 'noopener')}
    >
      {t('sidebar.viewOnGitHub')}
    </button>
    <a
      class="mx-[0.1rem] mb-[0.6rem] flex items-center justify-center gap-[0.3rem] text-center text-[0.8rem] text-muted-foreground hover:text-foreground"
      href="#docs"><span>{t('sidebar.exploreAPI')}</span><Icon name="external" size="0.85em" /></a
    >
  {/if}

  <button
    class="flex w-fit items-center justify-center self-start rounded-md border border-border p-[0.4rem] text-muted-foreground hover:border-white/20 hover:text-foreground"
    aria-label={t('sidebar.collapseLabel')}
    onclick={() => oko.setSidebarCollapsed(!oko.sidebarCollapsed)}
  >
    <Icon name={narrow ? 'close' : oko.sidebarCollapsed ? 'chevron-right' : 'chevron-left'} size="1em" />
  </button>
</aside>
