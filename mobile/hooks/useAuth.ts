/**
 * useAuth — JWT lifecycle + biometric unlock
 *
 * Flow on app launch:
 *   1. restoreSession() reads tokens from SecureStore
 *   2. If tokens exist → authenticateWithBiometric() → on pass, restore JWT to Zustand
 *   3. If biometric unavailable / not enrolled → skip it (fallback to password)
 *   4. If no tokens → redirect to login screen
 */

import * as LocalAuthentication from 'expo-local-authentication';
import * as SecureStore from 'expo-secure-store';
import { useCallback } from 'react';

import {
  apiClient,
  SECURE_ACCESS_KEY,
  SECURE_REFRESH_KEY,
} from '../services/api';
import { useAppStore, AuthUser } from '../stores/useAppStore';

export function useAuth() {
  const { setAuth, clearAuth, isAuthenticated } = useAppStore();

  /**
   * Standard email + password login.
   * Stores tokens in SecureStore and Zustand on success.
   */
  const loginWithPassword = useCallback(
    async (email: string, password: string) => {
      const data = await apiClient.login(email, password);

      await SecureStore.setItemAsync(SECURE_ACCESS_KEY,  data.access_token);
      await SecureStore.setItemAsync(SECURE_REFRESH_KEY, data.refresh_token);

      const user: AuthUser = {
        email,
        role:               data.role,
        mustChangePassword: data.must_change_password,
      };
      setAuth(user, data.access_token, data.refresh_token);
      return data;
    },
    [setAuth],
  );

  /**
   * On app launch: check SecureStore for tokens, refresh them to verify
   * they are still valid, then load into Zustand.
   * Returns true if a valid session was found.
   */
  const restoreSession = useCallback(async (): Promise<boolean> => {
    const accessToken  = await SecureStore.getItemAsync(SECURE_ACCESS_KEY);
    const refreshToken = await SecureStore.getItemAsync(SECURE_REFRESH_KEY);

    if (!accessToken || !refreshToken) return false;

    try {
      // Refresh validates the token and extends the session
      const data = await apiClient.refreshSession(refreshToken);

      await SecureStore.setItemAsync(SECURE_ACCESS_KEY,  data.access_token);
      await SecureStore.setItemAsync(SECURE_REFRESH_KEY, data.refresh_token);

      // We don't have the user object at this point — build a minimal one from
      // the refresh response (role is returned by the refresh endpoint).
      // Email is extracted from the access token payload (middle segment, base64).
      let email = '';
      try {
        const payload = JSON.parse(atob(data.access_token.split('.')[1]));
        email = payload.sub ?? '';
      } catch {}

      const user: AuthUser = {
        email,
        role:               data.role,
        mustChangePassword: data.must_change_password,
      };
      setAuth(user, data.access_token, data.refresh_token);
      return true;
    } catch {
      return false;
    }
  }, [setAuth]);

  /**
   * Trigger Face ID / fingerprint prompt.
   * Returns true immediately if the device has no biometric hardware or
   * no enrolled credentials (do not block login in that case).
   */
  const authenticateWithBiometric = useCallback(async (): Promise<boolean> => {
    const hasHardware  = await LocalAuthentication.hasHardwareAsync();
    const isEnrolled   = await LocalAuthentication.isEnrolledAsync();

    if (!hasHardware || !isEnrolled) {
      // Device can't do biometrics — skip silently
      return true;
    }

    const result = await LocalAuthentication.authenticateAsync({
      promptMessage:          'Authenticate to access OmniDBA',
      fallbackLabel:          'Use passcode',
      disableDeviceFallback:  false,
      cancelLabel:            'Cancel',
    });

    return result.success;
  }, []);

  /**
   * Log out: unregister push token, wipe SecureStore, clear Zustand.
   */
  const logout = useCallback(async () => {
    await apiClient.unregisterPushToken().catch(() => {});  // best-effort
    await SecureStore.deleteItemAsync(SECURE_ACCESS_KEY).catch(() => {});
    await SecureStore.deleteItemAsync(SECURE_REFRESH_KEY).catch(() => {});
    clearAuth();
  }, [clearAuth]);

  return {
    isAuthenticated,
    loginWithPassword,
    restoreSession,
    authenticateWithBiometric,
    logout,
  };
}
