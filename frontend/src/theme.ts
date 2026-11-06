// Site theme: stored per browser, applied as <html data-theme="...">. Charts subscribe to changes.
import { useSyncExternalStore } from 'react'

export const THEMES = [
  { key: 'falcon', name: 'Falcon Blue', note: 'Academy blue and silver, crisp and formal', swatch: ['#00205b', '#0033a0', '#a9b4c2'] },
  { key: 'sky', name: 'Open Sky', note: 'Bright sky blues with golden yellow', swatch: ['#003a9e', '#3d9bff', '#ffb81c'] },
  { key: 'steel', name: 'Chapel Steel', note: 'Modernist graphite and aluminum, sharp edges', swatch: ['#1b2028', '#24456e', '#9aa5b1'] },
  { key: 'night', name: 'Night Flight', note: 'Dark cockpit: deep navy, glowing blue, gold', swatch: ['#070d18', '#3d7bff', '#f2b632'] },
] as const
export type ThemeKey = (typeof THEMES)[number]['key']

const KEY = 'spark.theme'
const listeners = new Set<() => void>()

function read(): ThemeKey {
  try {
    const v = localStorage.getItem(KEY)
    if (v && THEMES.some((t) => t.key === v)) return v as ThemeKey
  } catch { /* storage unavailable: use the default */ }
  return 'falcon'
}

let current: ThemeKey = read()

export function applyTheme(t: ThemeKey = current) {
  if (t === 'falcon') document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', t)
}

export function setTheme(t: ThemeKey) {
  current = t
  try { localStorage.setItem(KEY, t) } catch { /* not saved; still applies for this visit */ }
  applyTheme(t)
  listeners.forEach((l) => l())
}

export function useTheme(): ThemeKey {
  return useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb) }, () => current)
}

/** Read a CSS custom property from the active theme. */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
