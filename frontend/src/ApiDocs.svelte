<script lang="ts">
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
    { "timestamp": "2026-09-04T06:00:00Z", "value": 312, "value_lifecycle": 344, "confidence": "high" }
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
  { "timestamp": "2026-09-04T05:00:00Z", "value": 298.0, "value_lifecycle": 331.0, "method": "flow_trace" }
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
      <a class="text-[var(--accent-color)] hover:underline" href="https://github.com/tilalx/oko#readme" target="_blank" rel="noopener">README</a>
      for methodology.
    </p>

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
        <section class="overflow-hidden rounded-2xl border border-border bg-[var(--card-translucent)] shadow-[0_20px_50px_rgba(0,0,0,0.5)]">
          <div class="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
            <span class="rounded-md bg-[rgba(70,196,145,0.15)] px-2 py-1 font-mono text-[0.72rem] font-bold text-[var(--accent-color)]">
              {ep.method}
            </span>
            <code class="font-mono text-[0.92rem]">{ep.path}</code>
            <span class="text-[0.85rem] text-muted-foreground">{ep.summary}</span>
          </div>
          <div class="grid gap-4 px-4 py-4 md:grid-cols-2">
            <div>
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
            </div>
            <pre class="overflow-x-auto rounded-lg bg-black/40 p-3 text-[0.74rem] leading-[1.5] text-[#c9d6c9]"><code>{ep.example}</code></pre>
          </div>
        </section>
      {/each}
    </div>

    <h2 class="mt-10 mb-3 text-[1.05rem] font-semibold">Field notes</h2>
    <div class="overflow-hidden rounded-2xl border border-border bg-[var(--card-translucent)]">
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
      <a class="text-[var(--accent-color)] hover:underline" href="https://github.com/tilalx/oko" target="_blank" rel="noopener">github.com/tilalx/oko</a>.
    </p>
  </main>
</div>
