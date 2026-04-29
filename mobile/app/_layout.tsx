/**
 * Root layout — auth guard + push notification setup.
 *
 * On every app launch:
 *   1. Attempt to restore session from SecureStore (token refresh)
 *   2. If session found → biometric prompt → navigate to tabs
 *   3. If no session or biometric fails → navigate to login
 *
 * Push notifications are initialised after authentication.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SplashScreen, Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import React, { useEffect, useRef, useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import { usePushNotifications } from '../hooks/usePushNotifications';
import { useAppStore } from '../stores/useAppStore';

SplashScreen.preventAutoHideAsync();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

function AuthGuard({ children }: { children: React.ReactNode }) {
  const router                            = useRouter();
  const segments                          = useSegments();
  const { restoreSession, authenticateWithBiometric } = useAuth();
  const { isAuthenticated }               = useAppStore();
  const [checked, setChecked]             = useState(false);

  // Register push notifications once authenticated
  usePushNotifications();

  useEffect(() => {
    (async () => {
      try {
        const hasSession = await restoreSession();

        if (hasSession) {
          const biometricOk = await authenticateWithBiometric();
          if (!biometricOk) {
            // Biometric explicitly cancelled — show login
            router.replace('/(auth)/login');
            return;
          }
          router.replace('/(tabs)/');
        } else {
          router.replace('/(auth)/login');
        }
      } catch {
        router.replace('/(auth)/login');
      } finally {
        setChecked(true);
        SplashScreen.hideAsync();
      }
    })();
    // Run once on mount only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // After initial check, also respond to isAuthenticated changes
  useEffect(() => {
    if (!checked) return;
    const inAuth = segments[0] === '(auth)';
    if (isAuthenticated && inAuth) {
      router.replace('/(tabs)/');
    } else if (!isAuthenticated && !inAuth) {
      router.replace('/(auth)/login');
    }
  }, [isAuthenticated, checked, segments, router]);

  return <>{children}</>;
}

export default function RootLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <StatusBar style="light" />
      <AuthGuard>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="(auth)"   options={{ headerShown: false }} />
          <Stack.Screen name="(tabs)"   options={{ headerShown: false }} />
          <Stack.Screen
            name="hitl-approval"
            options={{
              presentation:     'modal',
              headerShown:      true,
              headerTitle:      'Backup Approval',
              headerStyle:      { backgroundColor: '#161B22' },
              headerTintColor:  '#E6EDF3',
            }}
          />
        </Stack>
      </AuthGuard>
    </QueryClientProvider>
  );
}
