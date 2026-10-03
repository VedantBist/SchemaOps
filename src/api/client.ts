/**
 * The only way the UI talks to CausalOps: every call goes to the platform API under /api (one
 * origin, proxied by Vite in development and nginx in production). Non-2xx responses and network
 * failures become ApiError, so views show a real error instead of invented data.
 */

const TOKEN_KEY = 'causalops.token';

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function onUnauthorized(handler: () => void) {
  unauthorizedHandler = handler;
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session lasts for this page only */
  }
}

export function apiUrl(path: string, params?: Record<string, string | number | boolean | undefined | null>): string {
  const url = new URL(`/api${path}`, window.location.origin);
  Object.entries(params ?? {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
  });
  return url.pathname + url.search;
}

export async function request<T>(method: string, path: string, options: {
  params?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  auth?: boolean;
} = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const token = getToken();
  if (token && options.auth !== false) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(apiUrl(path, options.params), {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (e) {
    throw new ApiError(0, `CausalOps API unreachable (${(e as Error).message})`);
  }
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401 && options.auth !== false) unauthorizedHandler?.();
    const message = (data && typeof data === 'object' && 'message' in data && (data as { message?: string }).message)
      || (typeof data === 'string' && data) || `HTTP ${res.status}`;
    throw new ApiError(res.status, String(message), data);
  }
  return data as T;
}

export const get = <T>(path: string, params?: Record<string, string | number | boolean | undefined | null>) =>
  request<T>('GET', path, { params });
export const post = <T>(path: string, body?: unknown, params?: Record<string, string | number | boolean | undefined | null>) =>
  request<T>('POST', path, { body: body ?? {}, params });
export const put = <T>(path: string, body?: unknown, params?: Record<string, string | number | boolean | undefined | null>) =>
  request<T>('PUT', path, { body: body ?? {}, params });

/** Some stored fields may still arrive as JSON text from older servers; accept both. */
export function asJson<T>(value: unknown, fallback: T): T {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'string') {
    try {
      return JSON.parse(value) as T;
    } catch {
      return fallback;
    }
  }
  return value as T;
}
