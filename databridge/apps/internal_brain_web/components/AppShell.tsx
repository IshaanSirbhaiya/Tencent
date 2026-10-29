'use client';
// Compatibility layer for the pre-finals pages (/, /map, /compare, /audit, /sim). They keep
// importing from here; everything now comes from the new shell in components/shell/.
export { AppShell } from './shell/AppShell';
export { useMe } from './shell/Providers';
export { DEMO_COMPARE } from './shell/nav';

export const ROLE_LABEL: Record<string, string> = {
  employee: 'Employee',
  compliance_officer: 'Compliance officer',
  admin_it: 'IT admin',
};
