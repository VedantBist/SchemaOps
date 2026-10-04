import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { ApiError, get, onUnauthorized, post, setToken } from '../api/client';
import { api, type Environment } from '../api/causalops';
import { HOME_PAGE, visiblePage } from '../config/uiMode';

// ── routing (hash based, deep-linkable) ─────────────────────────────────────
export interface Route { page: string; id?: string; query: URLSearchParams }

export function parseHash(hash: string = window.location.hash): Route {
  const raw = hash.replace(/^#\/?/, '');
  const [path, qs] = raw.split('?');
  const [page, id] = path.split('/');
  const requested = page || HOME_PAGE;
  const visible = visiblePage(requested);
  return { page: visible, id: visible === requested && id ? decodeURIComponent(id) : undefined,
    query: new URLSearchParams(visible === requested ? qs ?? '' : '') };
}

export function useRoute(): [Route, (path: string) => void] {
  const [route, setRoute] = useState<Route>(() => parseHash());
  useEffect(() => {
    const on = () => {
      const requested = window.location.hash.replace(/^#\/?/, '').split(/[/?]/)[0];
      if (requested && visiblePage(requested) !== requested) {
        window.history.replaceState(null, '', `#/${HOME_PAGE}`);
      }
      setRoute(parseHash());
    };
    on();
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  const navigate = useCallback((path: string) => {
    window.location.hash = path.startsWith('/') ? path : `/${path}`;
  }, []);
  return [route, navigate];
}

// ── auth ─────────────────────────────────────────────────────────────────────
export type Role = 'VIEWER' | 'OPERATOR' | 'ADMIN';
export interface User { username: string; role: Role; mustChangePassword: boolean }

interface AuthState {
  user: User | null;
  /** The API has no authentication endpoints (sign-in arrives with Phase 6): local single-user mode. */
  authDisabled: boolean;
  checking: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
  can: (role: Role) => boolean;
}

const AuthCtx = createContext<AuthState | null>(null);
const RANK: Record<Role, number> = { VIEWER: 0, OPERATOR: 1, ADMIN: 2 };

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [authDisabled, setAuthDisabled] = useState(false);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      setUser(await get<User>('/auth/me'));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setAuthDisabled(true);
        setUser({ username: 'local operator', role: 'ADMIN', mustChangePassword: false });
      } else if (e instanceof ApiError && e.status === 401) {
        logout();
      }
    } finally {
      setChecking(false);
    }
  }, [logout]);

  useEffect(() => {
    onUnauthorized(logout);
    refreshUser();
  }, [logout, refreshUser]);

  const login = useCallback(async (username: string, password: string) => {
    const r = await post<{ token: string; user: User }>('/auth/login', { username, password });
    setToken(r.token);
    setUser(r.user);
  }, []);

  const can = useCallback((role: Role) => !!user && RANK[user.role] >= RANK[role], [user]);
  const value = useMemo(() => ({ user, authDisabled, checking, login, logout, refreshUser, can }),
    [user, authDisabled, checking, login, logout, refreshUser, can]);
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
};

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error('useAuth outside AuthProvider');
  return ctx;
}

// ── environment ─────────────────────────────────────────────────────────────
interface EnvState {
  environments: Environment[];
  env: Environment | null;
  envId: string | undefined;
  select: (id: string) => void;
  refresh: () => Promise<void>;
  error: ApiError | null;
  loaded: boolean;
}

const EnvCtx = createContext<EnvState | null>(null);
const ENV_KEY = 'causalops.environment';

export const EnvironmentProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [selected, setSelected] = useState<string | undefined>(() => {
    try { return localStorage.getItem(ENV_KEY) ?? undefined; } catch { return undefined; }
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setEnvironments(await api.environments());
      setError(null);
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  const env = environments.find((e) => e.id === selected) ?? environments.find((e) => e.status !== 'DISABLED') ?? null;
  const select = useCallback((id: string) => {
    setSelected(id);
    try { localStorage.setItem(ENV_KEY, id); } catch { /* per-tab only */ }
  }, []);

  const value = useMemo(() => ({ environments, env, envId: env?.id, select, refresh, error, loaded }),
    [environments, env, select, refresh, error, loaded]);
  return <EnvCtx.Provider value={value}>{children}</EnvCtx.Provider>;
};

export function useEnv(): EnvState {
  const ctx = useContext(EnvCtx);
  if (!ctx) throw new Error('useEnv outside EnvironmentProvider');
  return ctx;
}
