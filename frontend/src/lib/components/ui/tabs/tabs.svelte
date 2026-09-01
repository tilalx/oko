<script lang="ts">
  import { Tabs as TabsPrimitive } from 'bits-ui'
  import { cn } from '$lib/utils'

  let {
    value = $bindable(),
    items,
    class: className,
    size = 'default',
    ...restProps
  }: {
    value: string
    items: { value: string; label: string }[]
    class?: string
    /** "default" = card header pill tabs; "sm" = the smaller mix toggle. */
    size?: 'default' | 'sm'
  } & Omit<TabsPrimitive.RootProps, 'value'> = $props()
</script>

<TabsPrimitive.Root bind:value {...restProps} class={cn('contents', className)}>
  <TabsPrimitive.List
    class={cn(
      'flex w-fit gap-1 rounded-full bg-white/5 p-[0.15rem]',
      size === 'default' && 'mx-4 mb-[0.9rem] w-auto'
    )}
  >
    {#each items as item (item.value)}
      <TabsPrimitive.Trigger
        value={item.value}
        class={cn(
          'rounded-full border-none bg-transparent font-semibold text-muted-foreground outline-none transition-colors data-[state=active]:bg-[var(--pill-active-bg)] data-[state=active]:text-[var(--pill-active-fg)]',
          size === 'default'
            ? 'flex-1 px-[0.6rem] py-[0.4rem] text-[0.83rem]'
            : 'px-[0.65rem] py-[0.3rem] text-[0.72rem]'
        )}
      >
        {item.label}
      </TabsPrimitive.Trigger>
    {/each}
  </TabsPrimitive.List>
</TabsPrimitive.Root>
