import type { Config } from 'tailwindcss';

// Every colour is a CSS variable from app/globals.css (dark default, light, high contrast), so
// alpha modifiers work: `bg-accent/10`, `border-pf-slack/40`. Never add a raw hex here or in a page.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // surfaces and lines
        bg: v('bg'),
        surface: v('surface'),
        raised: v('raised'),
        overlay: v('overlay'),
        line: v('line'),
        'line-strong': v('line-strong'),
        // text
        fg: v('fg'),
        muted: v('muted'),
        subtle: v('subtle'),
        // the one accent
        accent: v('accent'),
        'accent-fg': v('accent-fg'),
        'accent-solid': v('accent-solid'),
        focus: v('focus'),
        // status (always with icon + text)
        ok: v('ok'),
        warn: v('warn'),
        bad: v('bad'),
        error: v('bad'),
        // platform identity
        pf: {
          confluence: v('pf-confluence'),
          jira: v('pf-jira'),
          slack: v('pf-slack'),
          drive: v('pf-drive'),
        },
        // permission state
        perm: {
          full: v('perm-full'),
          partial: v('perm-partial'),
          locked: v('perm-locked'),
        },
      },
      fontFamily: {
        sans: [
          '"Inter Variable"',
          'Inter',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          '"Segoe UI"',
          '"PingFang SC"',
          '"Noto Sans SC"',
          '"Microsoft YaHei"',
          'Roboto',
          'Helvetica',
          'Arial',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', '"JetBrains Mono"', 'Menlo', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
      fontSize: {
        // legacy
        '2xs': ['0.75rem', { lineHeight: '1rem' }],
        // type scale (ui_foundation.md §2.3)
        micro: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.01em' }],
        caption: ['0.75rem', { lineHeight: '1.125rem' }],
        body: ['var(--fs-body)', { lineHeight: 'var(--lh-body)' }],
        'body-sm': ['var(--fs-body-sm)', { lineHeight: '1.5' }],
        'heading-sm': ['0.9375rem', { lineHeight: '1.4', fontWeight: '600' }],
        heading: ['1.125rem', { lineHeight: '1.4', letterSpacing: '-0.01em', fontWeight: '600' }],
        title: ['1.375rem', { lineHeight: '1.3', letterSpacing: '-0.015em', fontWeight: '600' }],
        display: ['1.875rem', { lineHeight: '1.2', letterSpacing: '-0.02em', fontWeight: '600' }],
        kpi: ['1.75rem', { lineHeight: '1.1', letterSpacing: '-0.02em', fontWeight: '600' }],
      },
      boxShadow: {
        pop: 'var(--shadow-pop)',
      },
      transitionDuration: {
        fast: '120ms',
        base: '160ms',
        slow: '200ms',
      },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.2, 0, 0, 1)',
      },
      spacing: {
        topbar: 'var(--topbar-h)',
        sidenav: 'var(--sidenav-w)',
      },
      minHeight: {
        target: '1.5rem',
      },
      minWidth: {
        target: '1.5rem',
      },
    },
  },
  plugins: [],
};
export default config;
