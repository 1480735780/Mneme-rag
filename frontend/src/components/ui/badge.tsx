import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center justify-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 [&>svg]:size-3 [&>svg]:shrink-0 *:[svg]:pointer-events-none transition-[color,box-shadow] overflow-hidden [a&]:cursor-pointer [a&]:hover:border-foreground/30",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground [a&]:hover:bg-primary/80",
        secondary: "border-transparent bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/80",
        destructive: "border-transparent bg-destructive/10 text-destructive",
        outline: "border-foreground/20 text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({ className, variant, ...props }: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
