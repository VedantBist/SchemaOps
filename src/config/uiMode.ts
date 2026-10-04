/** Presentation only: set false and rebuild to restore the complete CausalOps UI. */
export const ULPF_DEMO_MODE: boolean = true;
export const LOG_PRODUCT_NAME = 'SchemaOps';

export const HOME_PAGE = ULPF_DEMO_MODE ? 'log-sources' : 'overview';

export function visiblePage(page: string): string {
  return ULPF_DEMO_MODE && !page.startsWith('log-') ? HOME_PAGE : page;
}
