<script lang="ts">
  import { Popover as PopoverPrimitive } from 'bits-ui'
  import { cn } from '$lib/utils'
  import type { Snippet } from 'svelte'

  let {
    open = $bindable(false),
    trigger,
    children,
    class: className,
    side = 'bottom',
    align = 'end',
    sideOffset = 8,
  }: {
    open?: boolean
    trigger: Snippet
    children: Snippet
    class?: string
    side?: PopoverPrimitive.ContentProps['side']
    align?: PopoverPrimitive.ContentProps['align']
    sideOffset?: number
  } = $props()
</script>

<PopoverPrimitive.Root bind:open>
  <PopoverPrimitive.Trigger>
    {@render trigger()}
  </PopoverPrimitive.Trigger>
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      {side}
      {align}
      {sideOffset}
      class={cn(
        'z-[500] flex w-[200px] flex-col gap-2 rounded-lg border border-border bg-[var(--card-translucent)] p-3 text-[0.8rem] text-foreground shadow-[0_4px_14px_rgba(0,0,0,0.28)] backdrop-blur-md outline-none',
        className
      )}
    >
      {@render children()}
    </PopoverPrimitive.Content>
  </PopoverPrimitive.Portal>
</PopoverPrimitive.Root>
