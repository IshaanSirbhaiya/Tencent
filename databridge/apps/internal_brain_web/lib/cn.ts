import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// Teach tailwind-merge our custom font-size names, otherwise `text-body` would be treated as a
// colour and silently dropped next to `text-fg`.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['2xs', 'micro', 'caption', 'body', 'body-sm', 'heading-sm', 'heading', 'title', 'display', 'kpi'] }],
    },
  },
});

/** Compose class names; later Tailwind classes win over earlier conflicting ones. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
