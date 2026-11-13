import { router } from 'expo-router';
import type { SuggestedAction } from '../types';
import type { IconName } from './components';

export const TOOL_LABEL: Record<string, string> = {
  release_production_order: 'Production order',
  request_budget_override: 'Budget override',
  set_standing_order: 'Standing order',
  create_supplier_return: 'Supplier return',
  log_line_check: 'Line check',
  request_overtime_shift: 'Overtime shift',
};

export const TOOL_ICON: Record<string, IconName> = {
  release_production_order: 'construct',
  request_budget_override: 'shield-checkmark',
  set_standing_order: 'repeat',
  create_supplier_return: 'return-down-back',
  log_line_check: 'clipboard',
  request_overtime_shift: 'time',
};

const SCREENS: Record<string, string> = { route: '/(tabs)', products: '/(tabs)/products', assistant: '/(tabs)/assistant', orders: '/(tabs)/orders' };

/** Open the screen behind an assistant suggestion or a card CTA. */
export function openAction(a: SuggestedAction, title?: string) {
  if (a.screen) {
    const pathname = SCREENS[a.screen] ?? `/${a.screen}`;
    router.push({ pathname: pathname as never, params: Object.fromEntries(Object.entries(a.args ?? {}).map(([k, v]) => [k, String(v)])) });
    return;
  }
  if (a.tool) {
    router.push({
      pathname: '/confirm',
      params: { tool: a.tool, args: JSON.stringify(a.args ?? {}), title: title ?? a.label },
    });
  }
}
