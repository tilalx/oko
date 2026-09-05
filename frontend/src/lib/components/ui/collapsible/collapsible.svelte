<script lang="ts">
  import { Collapsible as CollapsiblePrimitive } from 'bits-ui'
  import Icon from '../../Icon.svelte'
  import type { Snippet } from 'svelte'

  let {
    open = $bindable(false),
    trigger,
    children,
  }: { open?: boolean; trigger: Snippet; children: Snippet } = $props()
</script>

<CollapsiblePrimitive.Root bind:open class="mx-[0.1rem] my-[0.15rem]">
  <CollapsiblePrimitive.Trigger
    class="flex w-full items-center justify-between gap-2 truncate rounded-lg px-[0.6rem] py-2 text-[0.87rem] text-muted-foreground hover:bg-white/5 hover:text-foreground"
  >
    {@render trigger()}
    <Icon name="chevron-right" size="0.8em" class="shrink-0 transition-transform {open ? 'rotate-90' : ''}" />
  </CollapsiblePrimitive.Trigger>
  <CollapsiblePrimitive.Content class="pl-2">
    {@render children()}
  </CollapsiblePrimitive.Content>
</CollapsiblePrimitive.Root>
