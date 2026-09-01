<script lang="ts">
  import { Chart, type ChartConfiguration } from 'chart.js/auto'
  import { oko } from '$lib/state.svelte'
  import { CONFIDENCE_COLOR } from '$lib/constants'
  import { formatWeekdayTime } from '$lib/format'
  import type { ForecastPayload } from '$lib/api'

  let { payload }: { payload: ForecastPayload | null } = $props()

  let canvas: HTMLCanvasElement
  let chart: Chart<'line'> | null = null

  const points = $derived(payload?.forecast ?? [])
  const hasLifecycle = $derived(points.some((p) => p.value_lifecycle != null))
  const forecastSub = $derived(
    points.length ? `${points.length}h ahead · model ${payload?.model_version}` : 'No forecast yet.'
  )
  const forecastHighlightIndex = $derived(oko.horizonIndex - oko.historyLength(oko.selectedZone))

  function buildChart() {
    chart?.destroy()
    if (!canvas || !points.length) {
      chart = null
      return
    }
    const datasets: ChartConfiguration<'line'>['data']['datasets'] = [
      {
        label: 'Direct',
        data: points.map((p) => p.value),
        borderColor: CONFIDENCE_COLOR.high,
        pointBackgroundColor: points.map((p) => CONFIDENCE_COLOR[p.confidence] || CONFIDENCE_COLOR.low),
        pointRadius: points.map((_, i) => (i === forecastHighlightIndex ? 5 : 2.5)),
        borderWidth: 2,
        tension: 0.25,
        fill: false,
      },
    ]
    if (hasLifecycle) {
      datasets.push({
        label: 'Lifecycle',
        data: points.map((p) => p.value_lifecycle as number),
        borderColor: '#8b968c',
        borderDash: [4, 3],
        pointRadius: 0,
        borderWidth: 1.5,
        tension: 0.25,
        fill: false,
      })
    }
    chart = new Chart<'line'>(canvas, {
      type: 'line',
      data: {
        labels: points.map((p) => formatWeekdayTime(new Date(p.timestamp), oko.use24h)),
        datasets,
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            ticks: { maxTicksLimit: 8, autoSkip: true, color: '#8b968c' },
            grid: { color: 'rgba(255,255,255,0.06)' },
          },
          y: {
            title: { display: true, text: 'gCO2eq/kWh', color: '#8b968c' },
            beginAtZero: true,
            ticks: { color: '#8b968c' },
            grid: { color: 'rgba(255,255,255,0.06)' },
          },
        },
      },
    })
  }

  // Rebuild whenever the forecast payload itself changes (new data/zone);
  // just re-highlight the scrub position on horizonIndex changes alone.
  $effect(() => {
    void payload
    buildChart()
    return () => chart?.destroy()
  })

  $effect(() => {
    if (!chart) return
    const idx = forecastHighlightIndex
    const dataset = chart.data.datasets[0]
    dataset.pointRadius = (dataset.data as number[]).map((_, i) => (i === idx ? 5 : 2.5))
    chart.update('none')
  })
</script>

<div class="mb-[0.9rem] flex items-baseline justify-between">
  <div>
    <h2 class="mb-[0.15rem] text-[0.95rem]">5-day forecast</h2>
    <div class="text-[0.75rem] text-muted-foreground">{forecastSub}</div>
  </div>
</div>
<canvas bind:this={canvas} height="180"></canvas>
<div class="my-[0.7rem] flex flex-wrap gap-[0.9rem] text-[0.74rem] text-muted-foreground">
  <span class="inline-flex items-center gap-[0.35rem]"
    ><i class="inline-block h-[0.55rem] w-[0.55rem] rounded-full bg-[var(--high)]"></i>high confidence (day 1)</span
  >
  <span class="inline-flex items-center gap-[0.35rem]"
    ><i class="inline-block h-[0.55rem] w-[0.55rem] rounded-full bg-[var(--medium)]"></i>medium (days 2–3)</span
  >
  <span class="inline-flex items-center gap-[0.35rem]"
    ><i class="inline-block h-[0.55rem] w-[0.55rem] rounded-full bg-[var(--low)]"></i>low (days 4–5)</span
  >
  {#if hasLifecycle}
    <span class="inline-flex items-center gap-[0.35rem]"
      ><i class="inline-block h-0 w-[0.7rem] border-t-[1.5px] border-dashed border-muted-foreground"></i>lifecycle (dashed)</span
    >
  {/if}
</div>
