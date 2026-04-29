/**
 * OmniDBA — API client
 *
 * All requests attach the JWT access token from Zustand store.
 * On 401, the client silently attempts a refresh; on refresh failure
 * it clears auth state so the root layout redirects to login.
 *
 * API base URL is stored in the Zustand store (settable from the Settings tab).
 */

import * as SecureStore from 'expo-secure-store';
import { useAppStore, AuthUser } from '../stores/useAppStore';

export const SECURE_ACCESS_KEY  = 'oracle_dba_access_token';
export const SECURE_REFRESH_KEY = 'oracle_dba_refresh_token';

// ── Response types ────────────────────────────────────────────────────────────

export interface LoginResponse {
  access_token:         string;
  refresh_token:        string;
  token_type:           string;
  role:                 'admin' | 'dba';
  must_change_password: boolean;
}

export interface QueryResponse {
  thread_id:    string;
  intent:       string | null;
  final_result: string | null;
  sql:          string | null;
  columns:      string[];
  rows:         unknown[][];
  query_error:  boolean;
  hitl_pending: boolean;
  hitl_payload: {
    message:      string;
    rman_script:  string;
    backup_params: Record<string, unknown>;
  } | null;
}

export interface ApproveResponse {
  thread_id:    string;
  final_result: string | null;
  hitl_pending: boolean;
  hitl_payload: QueryResponse['hitl_payload'];
  approved_by:  string;
}

export interface MetricResult {
  columns: string[];
  rows:    unknown[][];
  status:  'ok' | 'warning' | 'critical' | 'unknown';
  error:   string | null;
}

export interface HealthReport {
  generated_at: string;
  overall:      'ok' | 'warning' | 'critical';
  metrics:      Record<string, MetricResult>;
}

export interface ScheduledJob {
  job_id:       string;
  name:         string;
  cron_expr:    string;
  backup_query: string;
  created_by:   string;
  created_at:   string;
  enabled:      boolean;
  last_fired:   string | null;
}

export interface ApiStatus {
  api:    string;
  oracle: string;
  ollama: string;
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// ── Client ────────────────────────────────────────────────────────────────────

class OracleApiClient {
  private get baseUrl(): string {
    return useAppStore.getState().apiUrl.replace(/\/$/, '');
  }

  private get token(): string | null {
    return useAppStore.getState().accessToken;
  }

  async request<T>(
    path: string,
    options: RequestInit = {},
    skipAuth = false,
  ): Promise<T> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string> ?? {}),
    };

    if (!skipAuth && this.token) {
      headers['Authorization'] = `Bearer ${this.token}`;
    }

    let resp = await fetch(`${this.baseUrl}${path}`, { ...options, headers });

    // Auto-refresh on 401
    if (resp.status === 401 && !skipAuth) {
      const refreshed = await this._refresh();
      if (refreshed) {
        headers['Authorization'] = `Bearer ${this.token}`;
        resp = await fetch(`${this.baseUrl}${path}`, { ...options, headers });
      } else {
        useAppStore.getState().clearAuth();
        await SecureStore.deleteItemAsync(SECURE_ACCESS_KEY).catch(() => {});
        await SecureStore.deleteItemAsync(SECURE_REFRESH_KEY).catch(() => {});
        throw new ApiError(401, 'Session expired. Please log in again.');
      }
    }

    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail ?? detail; } catch {}
      throw new ApiError(resp.status, detail);
    }

    return resp.json() as Promise<T>;
  }

  private async _refresh(): Promise<boolean> {
    const rt = useAppStore.getState().refreshToken;
    if (!rt) return false;
    try {
      const data = await this.request<LoginResponse>(
        '/auth/refresh',
        { method: 'POST', body: JSON.stringify({ refresh_token: rt }) },
        true,
      );
      const { user } = useAppStore.getState();
      if (user) {
        useAppStore.getState().setAuth(user, data.access_token, data.refresh_token);
      }
      await SecureStore.setItemAsync(SECURE_ACCESS_KEY,  data.access_token);
      await SecureStore.setItemAsync(SECURE_REFRESH_KEY, data.refresh_token);
      return true;
    } catch {
      return false;
    }
  }

  // ── Auth ───────────────────────────────────────────────────────────────────

  login(email: string, password: string): Promise<LoginResponse> {
    return this.request<LoginResponse>(
      '/auth/login',
      { method: 'POST', body: JSON.stringify({ email, password }) },
      true,
    );
  }

  refreshSession(refreshToken: string): Promise<LoginResponse> {
    return this.request<LoginResponse>(
      '/auth/refresh',
      { method: 'POST', body: JSON.stringify({ refresh_token: refreshToken }) },
      true,
    );
  }

  changePassword(newPassword: string): Promise<{ message: string }> {
    return this.request('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ new_password: newPassword }),
    });
  }

  // ── Push ───────────────────────────────────────────────────────────────────

  registerPushToken(expoToken: string): Promise<{ message: string }> {
    return this.request('/push/register', {
      method: 'POST',
      body: JSON.stringify({ expo_token: expoToken }),
    });
  }

  unregisterPushToken(): Promise<{ message: string }> {
    return this.request('/push/register', { method: 'DELETE' });
  }

  // ── Query ──────────────────────────────────────────────────────────────────

  query(text: string, threadId?: string): Promise<QueryResponse> {
    return this.request<QueryResponse>('/query', {
      method: 'POST',
      body: JSON.stringify({ text, thread_id: threadId ?? null }),
    });
  }

  approve(threadId: string, response: string): Promise<ApproveResponse> {
    return this.request<ApproveResponse>('/approve', {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId, response }),
    });
  }

  // ── Health report ──────────────────────────────────────────────────────────

  getHealthReport(): Promise<HealthReport> {
    return this.request<HealthReport>('/health-report');
  }

  getStatus(): Promise<ApiStatus> {
    return this.request<ApiStatus>('/status', {}, true);
  }

  // ── Schedules ──────────────────────────────────────────────────────────────

  createSchedule(data: {
    name: string;
    cron_expr: string;
    backup_query: string;
  }): Promise<ScheduledJob> {
    return this.request<ScheduledJob>('/schedule', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  getSchedules(): Promise<ScheduledJob[]> {
    return this.request<ScheduledJob[]>('/schedules');
  }

  deleteSchedule(jobId: string): Promise<{ message: string }> {
    return this.request(`/schedule/${jobId}`, { method: 'DELETE' });
  }
}

export const apiClient = new OracleApiClient();
