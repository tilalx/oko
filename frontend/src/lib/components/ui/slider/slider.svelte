<script lang="ts">
  import { Slider as SliderPrimitive } from 'bits-ui'
  import { cn } from '$lib/utils'

  let {
    value = 0,
    min = 0,
    max = 100,
    step = 1,
    class: className,
    onValueChange,
  }: {
    value?: number
    min?: number
    max?: number
    step?: number
    class?: string
    onValueChange?: (value: number) => void
  } = $props()

  // bits-ui's Root has a $bindable `value` -- passing the `value` prop
  // plain (not `bind:`) only keeps it in sync until the user's first
  // drag, at which point bits-ui's own onValueChange setter "forks" its
  // internal value from ours and a later programmatic change here (e.g.
  // snapping the horizon back to "now" on zone change) stops reaching the
  // visible thumb. Mirror the external `value` into a local var, `bind:`
  // that to the Root, and re-sync it whenever the external prop changes
  // out from under us.
  let internal = $state(value)
  $effect(() => {
    internal = value
  })

  function handleChange(v: number) {
    internal = v
    onValueChange?.(v)
  }
</script>

<SliderPrimitive.Root
  type="single"
  bind:value={internal}
  {min}
  {max}
  {step}
  onValueChange={handleChange}
  class={cn('relative flex h-[1.1rem] w-full touch-none items-center select-none', className)}
>
  {#snippet children()}
    <span class="relative h-1.5 w-full grow overflow-hidden rounded-full bg-white/10">
      <SliderPrimitive.Range class="absolute h-full bg-[var(--accent-color)]" />
    </span>
    <SliderPrimitive.Thumb
      index={0}
      class="block size-4 shrink-0 rounded-full border border-border bg-[var(--accent-color)] shadow outline-none"
    />
  {/snippet}
</SliderPrimitive.Root>
