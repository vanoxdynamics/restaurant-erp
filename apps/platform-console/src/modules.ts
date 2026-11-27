import type { GlobalModule } from './api';

/** The modules a business of this type can have, in catalogue order. */
export function modulesFor(category: string, modules: GlobalModule[]): GlobalModule[] {
  return modules.filter((m) => m.business_types.includes(category));
}

/**
 * Modules grouped by the business types they serve (kitchen modules, the menu POS's modules, the dress shop's, the hardware
 * shop's), each group labelled with the type names.
 */
export function groupByBusinessType(modules: GlobalModule[], typeName: (key: string) => string): { label: string; modules: GlobalModule[] }[] {
  const groups = new Map<string, GlobalModule[]>();
  for (const m of modules) {
    const key = m.business_types.join(',');
    groups.set(key, [...(groups.get(key) ?? []), m]);
  }
  return [...groups.entries()].map(([key, list]) => ({ label: key.split(',').map(typeName).join(', '), modules: list }));
}

/**
 * Switch `key` on or off in a set of enabled module keys, keeping dependencies whole: switching on also switches on what it
 * needs, switching off also switches off what needs it. Core modules are always on and never change.
 */
export function toggleModule(enabled: Set<string>, key: string, on: boolean, modules: GlobalModule[]): Set<string> {
  const byKey = new Map(modules.map((m) => [m.key, m]));
  const next = new Set(enabled);
  const visit = (k: string, value: boolean) => {
    const m = byKey.get(k);
    if (!m || m.is_core || next.has(k) === value) return;
    if (value) next.add(k); else next.delete(k);
    if (value) m.depends_on.forEach((d) => visit(d, true));
    else modules.filter((o) => o.depends_on.includes(k)).forEach((o) => visit(o.key, false));
  };
  visit(key, on);
  return next;
}
