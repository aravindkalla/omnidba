/**
 * usePushNotifications — Expo push token registration and notification routing
 *
 * Call this once from the root layout after the user is authenticated.
 * It registers the device's Expo push token with the backend so the server
 * can send HITL approval notifications to all DBA devices.
 *
 * Deep-link routing: when a notification is tapped, the 'screen' field in
 * the notification data determines where the app navigates.
 *   { screen: 'hitl-approval', thread_id: 'xxx' } → /hitl-approval?thread_id=xxx
 */

import Constants from 'expo-constants';
import * as Notifications from 'expo-notifications';
import { useRouter } from 'expo-router';
import { useEffect, useRef } from 'react';
import { Platform } from 'react-native';

import { apiClient } from '../services/api';
import { useAppStore } from '../stores/useAppStore';

// Show banner + play sound while the app is in the foreground
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge:  false,
  }),
});

export function usePushNotifications(): void {
  const router              = useRouter();
  const { isAuthenticated } = useAppStore();
  const notificationListener      = useRef<Notifications.EventSubscription | null>(null);
  const notificationResponseListener = useRef<Notifications.EventSubscription | null>(null);

  useEffect(() => {
    if (!isAuthenticated) return;

    registerForPush().catch(console.warn);

    // Notification received while app is foregrounded
    notificationListener.current = Notifications.addNotificationReceivedListener(
      (notification) => {
        const data = notification.request.content.data as Record<string, unknown>;
        if (data?.screen === 'hitl-approval' && typeof data.thread_id === 'string') {
          // Store pending HITL so the banner in the chat screen becomes visible
          useAppStore.getState().setPendingHitl({
            threadId: data.thread_id,
            payload:  data.hitl_payload as any,
          });
        }
      },
    );

    // User tapped the notification (app was in background or closed)
    notificationResponseListener.current = Notifications.addNotificationResponseReceivedListener(
      (response) => {
        const data = response.notification.request.content.data as Record<string, unknown>;
        if (data?.screen === 'hitl-approval' && typeof data.thread_id === 'string') {
          router.push({
            pathname: '/hitl-approval',
            params:   { thread_id: data.thread_id },
          });
        }
      },
    );

    return () => {
      notificationListener.current?.remove();
      notificationResponseListener.current?.remove();
    };
  }, [isAuthenticated, router]);
}

async function registerForPush(): Promise<void> {
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('hitl-approvals', {
      name:       'HITL Approvals',
      importance: Notifications.AndroidImportance.MAX,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: '#1565C0',
    });
  }

  const { status: existing } = await Notifications.getPermissionsAsync();
  let finalStatus = existing;

  if (existing !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }

  if (finalStatus !== 'granted') {
    console.warn('Push notification permission not granted');
    return;
  }

  const projectId =
    Constants.expoConfig?.extra?.eas?.projectId ??
    Constants.easConfig?.projectId;

  if (!projectId || projectId === 'YOUR_EXPO_PROJECT_ID_HERE') {
    console.warn('EXPO_PROJECT_ID not set — push notifications will not work until configured');
    return;
  }

  try {
    const tokenData = await Notifications.getExpoPushTokenAsync({ projectId });
    await apiClient.registerPushToken(tokenData.data);
  } catch (err) {
    console.warn('Failed to register push token:', err);
  }
}
