import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded border border-border bg-surface px-3 text-sm text-fg",
        "placeholder:text-subtle focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full rounded border border-border bg-surface px-3 py-2 font-mono text-xs text-fg",
      "placeholder:text-subtle focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
