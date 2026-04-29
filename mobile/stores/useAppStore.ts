import { create } from 'zustand';

export interface AuthUser {
  email: string;
  role: 'admin' | 'dba';
  mustChangePassword: boolean;
}

export interface HitlPayload {
  threadId: string;
  payload: {
    message: string;
    rman_script: string;
    backup_params: Record<string, unknown>;
  };
}

interface AppState {
  // ── Authentication ────────────────────────────────────────────────────────
  user:            AuthUser | null;
  accessToken:     string | null;
  refreshToken:    string | null;
  isAuthenticated: boolean;

  // ── Active query state ────────────────────────────────────────────────────
  threadId:        string | null;

  // ── Pending HITL (populated from push notification deep-link) ────────────
  pendingHitl:     HitlPayload | null;

  // ── Settings ──────────────────────────────────────────────────────────────
  apiUrl:          string;

  // ── Actions ───────────────────────────────────────────────────────────────
  setAuth:         (user: AuthUser, access: string, refresh: string) => void;
  clearAuth:       () => void;
  setThreadId:     (id: string | null) => void;
  setPendingHitl:  (data: HitlPayload | null) => void;
  setApiUrl:       (url: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  user:            null,
  accessToken:     null,
  refreshToken:    null,
  isAuthenticated: false,
  threadId:        null,
  pendingHitl:     null,
  apiUrl:          'http://34.14.171.170:8000',

  setAuth: (user, accessToken, refreshToken) =>
    set({ user, accessToken, refreshToken, isAuthenticated: true }),

  clearAuth: () =>
    set({
      user:            null,
      accessToken:     null,
      refreshToken:    null,
      isAuthenticated: false,
      threadId:        null,
      pendingHitl:     null,
    }),

  setThreadId:    (threadId)    => set({ threadId }),
  setPendingHitl: (pendingHitl) => set({ pendingHitl }),
  setApiUrl:      (apiUrl)      => set({ apiUrl }),
}));
