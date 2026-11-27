import { describe, expect, it } from 'vitest';
import type { GlobalModule } from './api';
import { groupByBusinessType, modulesFor, toggleModule } from './modules';

const mod = (key: string, business_types: string[], extra: Partial<GlobalModule> = {}): GlobalModule => ({
  key, name: key, description: '', business_types, is_core: false, depends_on: [], globally_enabled: true, reason: null,
  organizations_with_access: 0, ...extra,
});

const catalogue = [
  mod('pos', ['cafe', 'restaurant'], { is_core: true }),
  mod('kds', ['cafe', 'restaurant'], { depends_on: ['pos'] }),
  mod('apparel', ['apparel'], { is_core: true }),
  mod('apparel_returns', ['apparel'], { depends_on: ['apparel'] }),
  mod('apparel_extra', ['apparel'], { depends_on: ['apparel_returns'] }),
];

describe('modules by business type', () => {
  it('lists only the modules of a type', () => {
    expect(modulesFor('apparel', catalogue).map((m) => m.key)).toEqual(['apparel', 'apparel_returns', 'apparel_extra']);
    expect(modulesFor('restaurant', catalogue).map((m) => m.key)).toEqual(['pos', 'kds']);
  });

  it('groups modules by the types they serve', () => {
    const groups = groupByBusinessType(catalogue, (k) => k.toUpperCase());
    expect(groups.map((g) => [g.label, g.modules.length])).toEqual([['CAFE, RESTAURANT', 2], ['APPAREL', 3]]);
  });

  it('keeps dependencies whole when toggling', () => {
    const on = toggleModule(new Set(), 'apparel_extra', true, catalogue);
    expect([...on].sort()).toEqual(['apparel_extra', 'apparel_returns']); // core apparel is implied, never stored
    const off = toggleModule(on, 'apparel_returns', false, catalogue);
    expect([...off]).toEqual([]); // what needs it goes too
  });
});
