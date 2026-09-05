<script lang="ts">
  import { onMount } from 'svelte'
  import Icon from '$lib/components/Icon.svelte'
  import { Tabs } from '$lib/components/ui/tabs'
  import { getZones } from '$lib/api'

  interface Field {
    name: string
    type: string
    note?: string
  }

  interface Endpoint {
    method: 'GET'
    path: string
    summary: string
    detail?: string
    params?: Field[]
    response: string
    example: string
  }

  const forecastFields = `{
  "zone": "DE-LU",
  "generated_at": "2026-09-04T06:00:00Z",
  "model_version": "2026-09-01",
  "unit": "gCO2eq/kWh",
  "training_rows": 4102,
  "current": {
    "timestamp": "2026-09-04T05:00:00Z",
    "power_breakdown_percent": { "wind": 41.2, "solar": 3.1, "gas": 18.4, "nuclear": 0, "coal": 9.8 },
    "renewable_percent": 51.9,
    "fossil_free_percent": 51.9,
    "emissions_breakdown_percent": { "gas": 62.1, "coal": 37.9 }
  },
  "forecast": [
    { "timestamp": "2026-09-04T06:00:00Z", "value": 312, "value_lifecycle": 344, "confidence": "high", "price_eur_per_mwh": 78.42 }
  ],
  "attribution": ["ENTSO-E Transparency Platform", "NOAA GFS"],
  "source": "https://github.com/tilalx/oko"
}`

  const endpoints: Endpoint[] = [
    {
      method: 'GET',
      path: '/{zone}.json',
      summary: "A zone's forecast export",
      detail:
        "DE-LU is also served at /de.json for backward compatibility. See GET /zones for the published zone list.",
      params: [{ name: 'zone', type: 'path', note: "e.g. FR, DK-DK1, DE-LU" }],
      response: 'ForecastPayload',
      example: forecastFields,
    },
    {
      method: 'GET',
      path: '/exchanges.json',
      summary: 'Latest cross-border physical flow snapshot',
      detail: 'One entry per border with at least one record this run.',
      response: 'ExchangesPayload',
      example: `{
  "generated_at": "2026-09-04T06:00:00Z",
  "exchanges": [
    { "zone_from": "DE-LU", "zone_to": "FR", "timestamp": "2026-09-04T05:00:00Z", "net_flow_mw": 1240 }
  ],
  "source": "https://github.com/tilalx/oko"
}`,
    },
    {
      method: 'GET',
      path: '/history/{zone}',
      summary: "A zone's recent observed (non-forecast) history",
      params: [
        { name: 'zone', type: 'path' },
        { name: 'hours', type: 'query', note: 'default 48, clamped server-side to a maximum window' },
      ],
      response: 'HistoryPoint[]',
      example: `[
  { "timestamp": "2026-09-04T05:00:00Z", "value": 298.0, "value_lifecycle": 331.0, "method": "flow_trace", "price_eur_per_mwh": 65.3 }
]`,
    },
    {
      method: 'GET',
      path: '/zones',
      summary: 'Every zone OKO publishes a forecast for, and whether each has data yet',
      response: 'ZonesResponse',
      example: `{
  "zones": [
    { "zone": "DE-LU", "available": true },
    { "zone": "FR", "available": true }
  ]
}`,
    },
    {
      method: 'GET',
      path: '/api/evcc/co2',
      summary: "DE-LU's forecast, reshaped for evcc's custom co2-tariff plugin",
      detail: '/api/evcc/co2/{zone} does the same for any other published zone.',
      params: [{ name: 'zone', type: 'path', note: 'optional, on the /{zone} variant' }],
      response: 'EvccRate[]',
      example: `[
  { "start": "2026-09-04T06:00:00Z", "end": "2026-09-04T07:00:00Z", "value": 312 }
]`,
    },
    {
      method: 'GET',
      path: '/healthz',
      summary: 'Plain liveness check',
      response: 'text/plain',
      example: 'ok',
    },
  ]

  function slugFor(path: string): string {
    return path.replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  }

  const origin = typeof window !== 'undefined' ? window.location.origin : ''

  let zones = $state<string[]>(['DE-LU'])
  onMount(() => {
    getZones().then((z) => {
      if (z.length) zones = z
    })
  })

  interface PanelState {
    zone: string
    hours: number
    tab: 'example' | 'live'
    codeTab: 'curl' | 'js' | 'python'
    loading: boolean
    result: string | null
    error: string | null
  }

  const panels = new Map<string, PanelState>()
  function panelFor(ep: Endpoint): PanelState {
    const existing = panels.get(ep.path)
    if (existing) return existing
    const created = $state<PanelState>({
      zone: 'DE-LU',
      hours: 48,
      tab: 'example',
      codeTab: 'curl',
      loading: false,
      result: null,
      error: null,
    })
    panels.set(ep.path, created)
    return created
  }

  function hasZoneParam(ep: Endpoint): boolean {
    return ep.path.includes('{zone}')
  }
  function hasHoursParam(ep: Endpoint): boolean {
    return ep.params?.some((p) => p.name === 'hours') ?? false
  }

  function pathFor(ep: Endpoint, panel: PanelState): string {
    let path = hasZoneParam(ep) ? ep.path.replace('{zone}', panel.zone) : ep.path
    if (hasHoursParam(ep)) path += `?hours=${panel.hours}`
    return path
  }

  async function runEndpoint(ep: Endpoint) {
    const panel = panelFor(ep)
    panel.loading = true
    panel.error = null
    panel.result = null
    panel.tab = 'live'
    const path = pathFor(ep, panel)
    try {
      const res = await fetch(path, { cache: 'no-store' })
      const text = await res.text()
      let pretty = text
      try {
        pretty = JSON.stringify(JSON.parse(text), null, 2)
      } catch {
        // not JSON (e.g. /healthz's plain "ok") -- show the raw body
      }
      if (res.ok) {
        panel.result = pretty
      } else {
        panel.error = `HTTP ${res.status}\n${pretty}`
      }
    } catch (e) {
      panel.error = `Request failed: ${e instanceof Error ? e.message : String(e)}`
    } finally {
      panel.loading = false
    }
  }

  function curlFor(path: string): string {
    return `curl "${origin}${path}"`
  }
  function jsFor(path: string): string {
    return `fetch("${origin}${path}")\n  .then((res) => res.json())\n  .then(console.log)`
  }
  function pythonFor(path: string): string {
    return `import requests\n\nrequests.get("${origin}${path}").json()`
  }
  function codeFor(lang: 'curl' | 'js' | 'python', path: string): string {
    return lang === 'curl' ? curlFor(path) : lang === 'js' ? jsFor(path) : pythonFor(path)
  }

  const schemaNotes: Field[] = [
    { name: 'value / value_lifecycle', type: 'int | float', note: 'gCO2eq/kWh, direct vs. lifecycle emissions. value_lifecycle is null until that zone\'s lifecycle model has bootstrapped.' },
    { name: 'confidence', type: '"high" | "medium" | "low"', note: 'high = day 1, medium = days 2-3, low = days 4-5.' },
    { name: 'method', type: '"flow_trace" | "one_hop_fallback" | null', note: 'Which technique produced a history point; null for rows persisted before this column existed.' },
    { name: 'timestamps', type: 'string', note: "ISO 8601, UTC, 'Z' suffix." },
  ]
</script>

<div class="h-full w-full overflow-y-auto bg-background text-foreground">
  <main class="mx-auto max-w-[860px] px-6 py-10">
    <h1 class="text-[1.6rem] font-bold">API reference</h1>
    <p class="mt-2 max-w-[62ch] text-[0.92rem] leading-[1.6] text-muted-foreground">
      Public, keyless JSON endpoints for OKO's CO2 intensity forecasts. No account, no API key, no rate limit
      beyond fair use. Every response below is described exactly as returned — see the
      <a class="inline-flex items-center gap-[0.2rem] text-[var(--accent-color)] hover:underline" href="https://github.com/tilalx/oko#readme" target="_blank" rel="noopener">README<Icon name="external" size="0.75em" /></a>
      for methodology.
    </p>

    <nav
      class="sticky top-0 z-10 -mx-6 mt-5 flex flex-wrap gap-[0.4rem] border-b border-border bg-[var(--card-translucent)] px-6 py-3 text-[0.74rem] backdrop-blur-md"
    >
      {#each endpoints as ep (ep.path)}
        <a
          class="oko-num rounded border border-border px-[0.5rem] py-[0.2rem] text-muted-foreground hover:border-white/25 hover:text-foreground"
          href="#{slugFor(ep.path)}">{ep.path}</a
        >
      {/each}
    </nav>

    <div class="mt-5 flex flex-wrap gap-3 text-[0.8rem]">
      <div class="rounded-lg border border-border bg-[var(--card-translucent)] px-3 py-2">
        <span class="text-muted-foreground">Base URL</span>
        <div class="font-mono">{typeof window !== 'undefined' ? window.location.origin : ''}</div>
      </div>
      <div class="rounded-lg border border-border bg-[var(--card-translucent)] px-3 py-2">
        <span class="text-muted-foreground">Auth</span>
        <div>none</div>
      </div>
      <div class="rounded-lg border border-border bg-[var(--card-translucent)] px-3 py-2">
        <span class="text-muted-foreground">Format</span>
        <div>JSON (UTF-8)</div>
      </div>
    </div>

    <h2 class="mt-10 mb-3 text-[1.05rem] font-semibold">Endpoints</h2>
    <div class="flex flex-col gap-4">
      {#each endpoints as ep (ep.path)}
        {@const panel = panelFor(ep)}
        <section
          id={slugFor(ep.path)}
          class="scroll-mt-6 overflow-hidden rounded-lg border border-border bg-[var(--card-translucent)]"
        >
          <div class="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
            <span class="oko-num rounded-md bg-[rgba(53,199,192,0.15)] px-2 py-1 text-[0.72rem] font-semibold text-[var(--accent-color)]">
              {ep.method}
            </span>
            <code class="font-mono text-[0.92rem]">{ep.path}</code>
            <span class="text-[0.85rem] text-muted-foreground">{ep.summary}</span>
          </div>
          <div class="px-4 py-4">
            {#if ep.detail}
              <p class="mb-3 text-[0.82rem] leading-[1.5] text-muted-foreground">{ep.detail}</p>
            {/if}
            {#if ep.params?.length}
              <table class="w-full text-left text-[0.78rem]">
                <thead class="text-muted-foreground">
                  <tr><th class="pb-1 pr-3 font-medium">Param</th><th class="pb-1 pr-3 font-medium">In</th><th class="pb-1 font-medium">Notes</th></tr>
                </thead>
                <tbody>
                  {#each ep.params as p (p.name)}
                    <tr class="border-t border-border/60">
                      <td class="py-1 pr-3 font-mono">{p.name}</td>
                      <td class="py-1 pr-3 text-muted-foreground">{p.type}</td>
                      <td class="py-1 text-muted-foreground">{p.note ?? ''}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
            <div class="mt-3 text-[0.78rem] text-muted-foreground">
              Response schema: <code class="font-mono text-foreground">{ep.response}</code>
            </div>

            <div class="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-black/20 p-2.5">
              {#if hasZoneParam(ep)}
                <select
                  bind:value={panel.zone}
                  class="oko-num rounded-md border border-border bg-transparent px-2 py-1 text-[0.76rem]"
                >
                  {#each zones as z (z)}
                    <option value={z}>{z}</option>
                  {/each}
                </select>
              {/if}
              {#if hasHoursParam(ep)}
                <label class="flex items-center gap-1 text-[0.76rem] text-muted-foreground">
                  hours
                  <input
                    type="number"
                    min="1"
                    bind:value={panel.hours}
                    class="oko-num w-[4.5rem] rounded-md border border-border bg-transparent px-2 py-1 text-foreground"
                  />
                </label>
              {/if}
              <span class="oko-num flex-1 truncate text-[0.76rem] text-muted-foreground">{pathFor(ep, panel)}</span>
              <button
                class="rounded-md bg-[var(--pill-active-bg)] px-3 py-1 text-[0.78rem] font-semibold text-[var(--pill-active-fg)] hover:opacity-90 disabled:opacity-50"
                disabled={panel.loading}
                onclick={() => runEndpoint(ep)}
              >
                {panel.loading ? 'Running…' : 'Run'}
              </button>
            </div>

            <div class="mt-3 mb-2 w-fit">
              <Tabs
                size="sm"
                bind:value={
                  () => panel.tab,
                  (v) => (panel.tab = v as 'example' | 'live')
                }
                items={[
                  { value: 'example', label: 'Example' },
                  { value: 'live', label: 'Live response' },
                ]}
              />
            </div>
            {#if panel.tab === 'example'}
              <pre class="overflow-x-auto rounded-lg bg-black/40 p-3 text-[0.74rem] leading-[1.5] text-[#c9d6c9]"><code>{ep.example}</code></pre>
            {:else if panel.error}
              <pre class="overflow-x-auto rounded-lg border border-[var(--low)]/40 bg-black/40 p-3 text-[0.74rem] leading-[1.5] text-[var(--low)]"><code>{panel.error}</code></pre>
            {:else if panel.result}
              <pre class="overflow-x-auto rounded-lg bg-black/40 p-3 text-[0.74rem] leading-[1.5] text-[#c9d6c9]"><code>{panel.result}</code></pre>
            {:else}
              <div class="rounded-lg border border-dashed border-border p-3 text-[0.78rem] text-muted-foreground">
                {panel.loading ? 'Running…' : 'Click "Run" to fetch a real response from this server.'}
              </div>
            {/if}

            <div class="mt-4 mb-2 w-fit">
              <Tabs
                size="sm"
                bind:value={
                  () => panel.codeTab,
                  (v) => (panel.codeTab = v as 'curl' | 'js' | 'python')
                }
                items={[
                  { value: 'curl', label: 'curl' },
                  { value: 'js', label: 'JS' },
                  { value: 'python', label: 'Python' },
                ]}
              />
            </div>
            <pre class="overflow-x-auto rounded-lg bg-black/40 p-3 text-[0.74rem] leading-[1.5] text-[#c9d6c9]"><code>{codeFor(panel.codeTab, pathFor(ep, panel))}</code></pre>
          </div>
        </section>
      {/each}
    </div>

    <h2 class="mt-10 mb-3 text-[1.05rem] font-semibold">Field notes</h2>
    <div class="overflow-hidden rounded-lg border border-border bg-[var(--card-translucent)]">
      <table class="w-full text-left text-[0.82rem]">
        <tbody>
          {#each schemaNotes as n (n.name)}
            <tr class="border-b border-border/60 last:border-0">
              <td class="w-[220px] px-4 py-3 align-top font-mono text-[0.78rem]">{n.name}</td>
              <td class="px-4 py-3 align-top leading-[1.5] text-muted-foreground">{n.note}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <h2 class="mt-10 mb-3 text-[1.05rem] font-semibold">Errors</h2>
    <p class="max-w-[62ch] text-[0.85rem] leading-[1.6] text-muted-foreground">
      <code class="font-mono text-foreground">404</code> for an unknown zone, <code class="font-mono text-foreground">503</code>
      when a zone hasn't produced a forecast yet (bootstrap, or every upstream fetch failed that run — retry later),
      <code class="font-mono text-foreground">400</code> for an invalid query parameter. Error bodies are
      <code class="font-mono text-foreground">{'{ "detail": "..." }'}</code>.
    </p>

    <p class="mt-10 border-t border-border pt-6 text-[0.8rem] text-muted-foreground">
      Self-hosted, keyless CO2 forecast. Source and data pipeline:
      <a class="inline-flex items-center gap-[0.2rem] text-[var(--accent-color)] hover:underline" href="https://github.com/tilalx/oko" target="_blank" rel="noopener">github.com/tilalx/oko<Icon name="external" size="0.75em" /></a>.
    </p>
  </main>
</div>
