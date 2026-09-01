<script lang="ts" module>
  import { type VariantProps, tv } from 'tailwind-variants'

  export const buttonVariants = tv({
    base: "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:pointer-events-none [&_svg]:shrink-0",
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:opacity-90',
        outline: 'border border-border bg-transparent text-foreground hover:bg-accent',
        ghost: 'bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground',
        icon: 'bg-[var(--card-translucent)] backdrop-blur-md border border-border text-foreground shadow-[0_8px_24px_rgba(0,0,0,0.3)] hover:bg-[#24271f]',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 rounded-md px-3',
        icon: 'size-9',
        'icon-sm': 'size-[34px] rounded-lg text-[0.95rem]',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  })

  export type ButtonVariant = VariantProps<typeof buttonVariants>['variant']
  export type ButtonSize = VariantProps<typeof buttonVariants>['size']
</script>

<script lang="ts">
  import type { HTMLButtonAttributes } from 'svelte/elements'
  import { cn } from '$lib/utils'

  let {
    class: className,
    variant = 'default',
    size = 'default',
    active = false,
    children,
    ...restProps
  }: HTMLButtonAttributes & {
    variant?: ButtonVariant
    size?: ButtonSize
    /** OKO's `.on` state (flow/layer toggles) -- accent border+color when active. */
    active?: boolean
  } = $props()
</script>

<button
  class={cn(
    buttonVariants({ variant, size }),
    active && 'text-[var(--accent-color)] border-[rgba(70,196,145,0.4)]',
    className
  )}
  {...restProps}
>
  {@render children?.()}
</button>
