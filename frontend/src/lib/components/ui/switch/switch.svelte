<script lang="ts">
  import { Switch as SwitchPrimitive } from 'bits-ui'
  import { cn } from '$lib/utils'

  let {
    checked = $bindable(false),
    class: className,
    ...restProps
  }: SwitchPrimitive.RootProps = $props()
</script>

<SwitchPrimitive.Root
  bind:checked
  class={cn(
    'peer inline-flex h-4 w-7 shrink-0 cursor-pointer items-center rounded-full border border-border transition-colors data-[state=unchecked]:bg-white/10 data-[state=checked]:bg-[var(--accent-color)]',
    className
  )}
  {...restProps}
>
  <SwitchPrimitive.Thumb
    class="pointer-events-none block size-3 translate-x-0.5 rounded-full bg-foreground transition-transform data-[state=checked]:translate-x-[14px]"
  />
</SwitchPrimitive.Root>
