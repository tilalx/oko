<script lang="ts" module>
  /** One consistent line-icon language for OKO's chrome -- replaces the
   * unicode/emoji glyphs that used to stand in for icons (nav, controls,
   * generation categories). Single stroke weight, single viewBox, drawn
   * flat so they read as instrument iconography rather than
   * illustrations. All markup here is a static, developer-authored
   * constant -- never user input -- so `{@html}` below is safe. */
  const PATHS: Record<string, string> = {
    // Navigation
    map: '<path d="M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5z"/><path d="M9 4v14.5"/><path d="M15 6.5V21"/>',
    code: '<path d="M8 7 3 12l5 5"/><path d="M16 7l5 5-5 5"/>',
    github:
      '<path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.89 1.53 2.34 1.09 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.56-1.11-4.56-4.95 0-1.09.39-1.99 1.03-2.69-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.03a9.6 9.6 0 0 1 5 0c1.91-1.3 2.75-1.03 2.75-1.03.55 1.37.2 2.39.1 2.64.64.7 1.03 1.6 1.03 2.69 0 3.85-2.34 4.7-4.57 4.94.36.31.68.92.68 1.85v2.75c0 .26.18.58.69.48A10 10 0 0 0 12 2Z"/>',
    settings:
      '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>',
    layers:
      '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
    gauge: '<path d="M4 15a8 8 0 1 1 16 0"/><path d="M12 15V9"/><path d="M9 4h6"/>',
    external: '<path d="M7 17 17 7"/><path d="M8 7h9v9"/>',

    // Controls
    "zoom-in": '<path d="M12 5v14M5 12h14"/>',
    "zoom-out": '<path d="M5 12h14"/>',
    back: '<path d="m15 5-7 7 7 7"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    "chevron-left": '<path d="m14 6-6 6 6 6"/>',
    "chevron-right": '<path d="m10 6 6 6-6 6"/>',
    flow: '<path d="M3 8h13"/><path d="m13 4 4 4-4 4"/><path d="M21 16H8"/><path d="m11 20-4-4 4-4"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M3 12h2M19 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
    moon: '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4 7 7 0 0 0 20 14.5Z"/>',
    play: '<path d="M7 5v14l12-7Z"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    coverage: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16"/>',
    target:
      '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
    docs: '<path d="M6 3h9l5 5v13H6Z"/><path d="M15 3v5h5"/><path d="M9 13h6M9 17h6"/>',

    // Generation categories -- flat geometric strokes, not mini pictograms,
    // so the set reads as one family (see CATEGORY_META in constants.ts).
    biomass: '<path d="M12 21c0-6 3-9 7-11-2 5-2 9-7 11Z"/><path d="M12 21c0-7-3-10-7-12 1 6 2 10 7 12Z"/>',
    geothermal: '<path d="M12 3c2 3 3 5 3 7a3 3 0 1 1-6 0c0-2 1-4 3-7Z"/><path d="M6 21c0-3 2.5-5 6-5s6 2 6 5"/>',
    hydro: '<path d="M12 3c3 4 6 7.5 6 11a6 6 0 0 1-12 0c0-3.5 3-7 6-11Z"/>',
    solar: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2.5M12 18.5V21M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M3 12h2.5M18.5 12H21M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8"/>',
    wind: '<path d="M3 8h9a2.5 2.5 0 1 0-2.2-3.6"/><path d="M3 16h13a2.5 2.5 0 1 1-2.2 3.6"/><path d="M3 12h6a2 2 0 1 0-1.8-2.8"/>',
    nuclear:
      '<circle cx="12" cy="12" r="1.6"/><path d="M12 12 6.3 8.7A6 6 0 0 0 12 18Z"/><path d="M12 12l5.7-3.3A6 6 0 0 0 6.3 8.7Z"/><path d="M12 12v6.9a6 6 0 0 0 5.7-7.2Z"/>',
    gas: '<path d="M12 3s-4 4.5-4 8.5a4 4 0 0 0 8 0c0-1.4-.8-2.4-1.5-3.3.2 1-.2 1.8-1 2C13.8 9 12 6.8 12 3Z"/>',
    coal: '<rect x="4" y="9" width="7" height="7" rx="1"/><rect x="12" y="6" width="8" height="10" rx="1"/>',
    oil: '<path d="M7 8h6l2 3v8H5v-8Z"/><path d="M9 8V5h2v3"/>',
    unknown: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.5 2.5 0 1 1 3.9 2.1c-.9.6-1.4 1.1-1.4 2.2"/><circle cx="12" cy="17" r="0.1" fill="currentColor"/>',
  }

  export type IconName = keyof typeof PATHS
</script>

<script lang="ts">
  let {
    name,
    size = '1em',
    strokeWidth = 1.6,
    class: className = '',
  }: { name: IconName; size?: string | number; strokeWidth?: number; class?: string } = $props()
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width={strokeWidth}
  stroke-linecap="round"
  stroke-linejoin="round"
  class={className}
  aria-hidden="true"
  >{@html PATHS[name] ?? ''}</svg
>
