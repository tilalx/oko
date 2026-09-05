<script lang="ts">
  import { Tooltip as TooltipPrimitive } from 'bits-ui'
  import type { Snippet } from 'svelte'

  let { text, children }: { text: string; children: Snippet } = $props()
</script>

<TooltipPrimitive.Provider delayDuration={150}>
  <TooltipPrimitive.Root>
    <TooltipPrimitive.Trigger>
      {@render children()}
    </TooltipPrimitive.Trigger>
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        sideOffset={6}
        class="z-[700] max-w-[220px] rounded-md border border-border bg-[var(--card-translucent)] px-2 py-1 text-[0.72rem] text-foreground shadow-[0_4px_14px_rgba(0,0,0,0.28)] backdrop-blur-md"
      >
        {text}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  </TooltipPrimitive.Root>
</TooltipPrimitive.Provider>
