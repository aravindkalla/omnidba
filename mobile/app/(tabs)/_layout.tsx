import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';
import React from 'react';
import { useAppStore } from '../../stores/useAppStore';
import { Colors } from '../../constants/theme';

export default function TabsLayout() {
  const { pendingHitl } = useAppStore();

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor:   Colors.primary,
        tabBarInactiveTintColor: Colors.textMuted,
        tabBarStyle: {
          backgroundColor: Colors.surface,
          borderTopColor:  Colors.border,
        },
        headerStyle:      { backgroundColor: Colors.surface },
        headerTitleStyle: { color: Colors.text, fontWeight: '700' },
        headerTintColor:  Colors.text,
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title:      'Chat',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="chatbubbles-outline" size={size} color={color} />
          ),
          // Show badge when an HITL approval is pending
          tabBarBadge: pendingHitl ? '!' : undefined,
        }}
      />
      <Tabs.Screen
        name="health"
        options={{
          title:      'Health',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="pulse-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="schedules"
        options={{
          title:      'Schedules',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="calendar-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title:      'Settings',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="settings-outline" size={size} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
