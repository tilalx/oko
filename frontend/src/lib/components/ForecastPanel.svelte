<script lang="ts">
  import { Chart, type ChartConfiguration } from 'chart.js/auto'
  import { oko } from '$lib/state.svelte'
  import { CONFIDENCE_COLOR } from '$lib/constants'
  import { formatWeekdayTime } from '$lib/format'
  import { t } from '$lib/i18n'
  import type { ForecastPayload } from '$lib/api'

  let { payload }: { payload: ForecastPayload | null } = $props()

  let canvas: HTMLCanvasElement
  let chart: Chart<'line'> | null = null

  const points = $derived(payload?.forecast ?? [])
  const forecastSub = $derived(
    points.length ? t('forecastPanel.offsetAndModel', { offset: points.length, version: payload?.model_version }) : t('forecastPanel.noForecastYet')
  )
  const forecastHighlightIndex = $derived(oko.horizonIndex - oko.historyLength(oko.selectedZone))

  const PRICE_LINE_COLOR = '#7aa6c2'

  function buildChart() {
    chart?.destroy()
    if (!canvas || !points.length) {
      chart = null
      return
    }
    const datasets: ChartConfiguration<'line'>['data']['datasets'] = [
      {
        label: 'Carbon intensity',
        data: points.map((p) => p.value_lifecycle ?? p.value),
        borderColor: CONFIDENCE_COLOR.high,
        pointBackgroundColor: points.map((p) => CONFIDENCE_COLOR[p.confidence] || CONFIDENCE_COLOR.low),
        pointRadius: points.map((_, i) => (i === forecastHighlightIndex ? 5 : 2.5)),
        borderWidth: 2,
        tension: 0.25,
        fill: false,
        yAxisID: 'y',
      },
      {
        label: 'Day-ahead price',
        data: points.map((p) => p.price_eur_per_mwh),
        borderColor: PRICE_LINE_COLOR,
        pointRadius: 0,
        borderWidth: 1.5,
        borderDash: [4, 3],
        tension: 0.25,
        fill: false,
        spanGaps: false,
        yAxisID: 'price',
      },
    ]
    chart = new Chart<'line'>(canvas, {
      type: 'line',
      data: {
        labels: points.map((p) => formatWeekdayTime(new Date(p.timestamp), oko.use24h, oko.locale)),
        datasets,
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            ticks: { maxTicksLimit: 8, autoSkip: true, color: '#8b968c', font: { family: 'IBM Plex Sans', size: 10 } },
            grid: { color: 'rgba(255,255,255,0.06)' },
          },
          y: {
            position: 'left',
            title: { display: true, text: 'gCO2eq/kWh', color: '#8b968c', font: { family: 'IBM Plex Sans', size: 10 } },
            beginAtZero: true,
            ticks: { color: '#8b968c', font: { family: 'IBM Plex Mono', size: 10 } },
            grid: { color: 'rgba(255,255,255,0.06)' },
          },
          price: {
            position: 'right',
            title: { display: true, text: 'EUR/MWh', color: PRICE_LINE_COLOR, font: { family: 'IBM Plex Sans', size: 10 } },
            ticks: { color: PRICE_LINE_COLOR, font: { family: 'IBM Plex Mono', size: 10 } },
            grid: { display: false },
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
    <h2 class="mb-[0.15rem] text-[0.95rem] font-semibold">{t('forecastPanel.title')}</h2>
    <div class="text-[0.75rem] text-muted-foreground">{forecastSub}</div>
  </div>
</div>
<canvas bind:this={canvas} height="180"></canvas>
<div class="my-[0.7rem] flex flex-wrap gap-[0.9rem] text-[0.74rem] text-muted-foreground">
  <span class="inline-flex items-center gap-[0.35rem]"
    ><i class="inline-block h-[0.55rem] w-[0.55rem] rounded-full bg-[var(--high)]"></i>{t('forecastPanel.confidenceHigh')}</span
  >
  <span class="inline-flex items-center gap-[0.35rem]"
    ><i class="inline-block h-[0.55rem] w-[0.55rem] rounded-full bg-[var(--medium)]"></i>{t('forecastPanel.confidenceMedium')}</span
  >
  <span class="inline-flex items-center gap-[0.35rem]"
    ><i class="inline-block h-[0.55rem] w-[0.55rem] rounded-full bg-[var(--low)]"></i>{t('forecastPanel.confidenceLow')}</span
  >
  <span class="inline-flex items-center gap-[0.35rem]"
    ><i class="inline-block h-[0.55rem] w-[0.1rem]" style="background:{PRICE_LINE_COLOR}"></i>{t('forecastPanel.dayAheadPrice')}</span
  >
</div>
