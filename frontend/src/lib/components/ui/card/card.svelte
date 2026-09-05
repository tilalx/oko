<script lang="ts">
  import { cn } from '$lib/utils'
  import type { Snippet } from 'svelte'

  let { class: className, children }: { class?: string; children: Snippet } = $props()
</script>

<section
  class={cn(
    'flex flex-col overflow-hidden rounded-lg border border-border bg-[var(--card-translucent)] shadow-[0_4px_14px_rgba(0,0,0,0.28)] backdrop-blur-[14px]',
    className
  )}
>
  {@render children()}
</section>
