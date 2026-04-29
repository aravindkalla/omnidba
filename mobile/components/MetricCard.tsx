/**
 * MetricCard — a single number metric tile for the Health Dashboard.
 * Background border changes colour based on status: ok → green, warning → amber, critical → red.
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Colors, FontSize, Radius, Spacing, StatusColor } from '../constants/theme';

interface Props {
  title:    string;
  value:    string | number;
  subtitle?: string;
  status:   'ok' | 'warning' | 'critical' | 'unknown';
}

export function MetricCard({ title, value, subtitle, status }: Props) {
  const accent = StatusColor[status] ?? Colors.unknown;

  return (
    <View style={[styles.card, { borderColor: accent }]}>
      <Text style={styles.title} numberOfLines={1}>{title}</Text>
      <Text style={[styles.value, { color: accent }]}>{value}</Text>
      {subtitle ? (
        <Text style={styles.subtitle} numberOfLines={1}>{subtitle}</Text>
      ) : null}
      <View style={[styles.badge, { backgroundColor: accent + '22' }]}>
        <Text style={[styles.badgeText, { color: accent }]}>
          {status.toUpperCase()}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flex:           1,
    minWidth:       140,
    backgroundColor: Colors.surface,
    borderRadius:   Radius.md,
    borderWidth:    1.5,
    padding:        Spacing.md,
    margin:         Spacing.xs,
    alignItems:     'flex-start',
  },
  title: {
    color:      Colors.textMuted,
    fontSize:   FontSize.sm,
    fontWeight: '500',
    marginBottom: Spacing.xs,
  },
  value: {
    fontSize:   FontSize.xxl,
    fontWeight: '700',
    marginBottom: Spacing.xs,
  },
  subtitle: {
    color:    Colors.textMuted,
    fontSize: FontSize.xs,
    marginBottom: Spacing.sm,
  },
  badge: {
    paddingVertical:   2,
    paddingHorizontal: Spacing.sm,
    borderRadius:      Radius.full,
  },
  badgeText: {
    fontSize:   FontSize.xs,
    fontWeight: '700',
  },
});
