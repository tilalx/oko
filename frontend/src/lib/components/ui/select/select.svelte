<script lang="ts">
  import { Select as SelectPrimitive } from 'bits-ui'
  import { cn } from '$lib/utils'

  let {
    value,
    items,
    onValueChange,
    class: className,
    triggerClass,
  }: {
    value: string
    items: { value: string; label: string }[]
    onValueChange?: (value: string) => void
    class?: string
    triggerClass?: string
  } = $props()

  // See slider.svelte's comment -- bits-ui's Root has a $bindable `value`
  // that forks from a plain (non-`bind:`) prop after the first user
  // selection, so a later programmatic change (e.g. clicking a zone on
  // the map) would stop reaching the trigger's displayed value without
  // this local re-sync.
  let internal = $state(value)
  $effect(() => {
    internal = value
  })

  function handleChange(v: string) {
    internal = v
    onValueChange?.(v)
  }
</script>

<SelectPrimitive.Root type="single" bind:value={internal} onValueChange={handleChange}>
  <SelectPrimitive.Trigger
    class={cn(
      'flex items-center justify-between gap-1 rounded-lg border border-border bg-white/6 px-2 py-1 text-[0.78rem] text-foreground outline-none max-w-[6.2rem]',
      triggerClass
    )}
  >
    <span class="truncate">{internal}</span>
    <span class="text-muted-foreground text-[0.65rem]">▾</span>
  </SelectPrimitive.Trigger>
  <SelectPrimitive.Portal>
    <SelectPrimitive.Content
      class={cn(
        'z-[700] max-h-72 overflow-y-auto rounded-lg border border-border bg-[var(--card-translucent)] p-1 text-sm text-foreground shadow-[0_8px_24px_rgba(0,0,0,0.4)] backdrop-blur-md',
        className
      )}
      sideOffset={4}
    >
      {#each items as item (item.value)}
        <SelectPrimitive.Item
          value={item.value}
          label={item.label}
          class="flex cursor-pointer items-center rounded-md px-2 py-1.5 text-[0.8rem] outline-none data-highlighted:bg-white/10 data-[selected]:text-[var(--accent-color)]"
        >
          {item.label}
        </SelectPrimitive.Item>
      {/each}
    </SelectPrimitive.Content>
  </SelectPrimitive.Portal>
</SelectPrimitive.Root>
