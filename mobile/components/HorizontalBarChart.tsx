/**
 * HorizontalBarChart — pure React Native, no charting library required.
 * Used for tablespace usage (PCT_USED) and top SQL (elapsed time).
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Colors, FontSize, Radius, Spacing, StatusColor } from '../constants/theme';

export interface BarEntry {
  label: string;
  value: number;
}

interface Props {
  title:    string;
  data:     BarEntry[];
  maxValue?: number;        // defaults to max value in data
  unit?:    string;         // shown next to value (e.g. "%" or "s")
  status:   'ok' | 'warning' | 'critical' | 'unknown';
  maxBars?: number;
}

export function HorizontalBarChart({
  title,
  data,
  maxValue,
  unit = '',
  status,
  maxBars = 8,
}: Props) {
  const displayed = data.slice(0, maxBars);
  const max       = maxValue ?? Math.max(...displayed.map((d) => d.value), 1);
  const accent    = StatusColor[status] ?? Colors.unknown;

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.title}>{title}</Text>
        <View style={[styles.badge, { backgroundColor: accent + '22' }]}>
          <Text style={[styles.badgeText, { color: accent }]}>{status.toUpperCase()}</Text>
        </View>
      </View>

      {displayed.length === 0 ? (
        <Text style={styles.empty}>No data</Text>
      ) : (
        displayed.map((entry, i) => {
          const pct = Math.max(0, Math.min(100, (entry.value / max) * 100));
          const barColor =
            pct >= 90 ? Colors.critical :
            pct >= 80 ? Colors.warning  :
            Colors.ok;
          return (
            <View key={i} style={styles.barRow}>
              <Text style={styles.label} numberOfLines={1}>{entry.label}</Text>
              <View style={styles.track}>
                <View
                  style={[
                    styles.fill,
                    { width: `${pct}%`, backgroundColor: barColor },
                  ]}
                />
              </View>
              <Text style={styles.valueText}>
                {typeof entry.value === 'number' ? entry.value.toFixed(1) : entry.value}{unit}
              </Text>
            </View>
          );
        })
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: Colors.surface,
    borderRadius:    Radius.md,
    borderWidth:     1,
    borderColor:     Colors.border,
    padding:         Spacing.md,
    marginVertical:  Spacing.xs,
  },
  header: {
    flexDirection:  'row',
    justifyContent: 'space-between',
    alignItems:     'center',
    marginBottom:   Spacing.sm,
  },
  title: {
    color:      Colors.text,
    fontSize:   FontSize.md,
    fontWeight: '600',
    flex:       1,
  },
  badge: {
    paddingVertical:   2,
    paddingHorizontal: Spacing.sm,
    borderRadius:      Radius.full,
    marginLeft:        Spacing.sm,
  },
  badgeText: {
    fontSize:   FontSize.xs,
    fontWeight: '700',
  },
  barRow: {
    flexDirection:  'row',
    alignItems:     'center',
    marginVertical: 3,
  },
  label: {
    color:     Colors.textMuted,
    fontSize:  FontSize.xs,
    width:     90,
    marginRight: Spacing.sm,
  },
  track: {
    flex:            1,
    height:          14,
    backgroundColor: Colors.border,
    borderRadius:    Radius.full,
    overflow:        'hidden',
  },
  fill: {
    height:       14,
    borderRadius: Radius.full,
  },
  valueText: {
    color:     Colors.text,
    fontSize:  FontSize.xs,
    width:     50,
    textAlign: 'right',
    marginLeft: Spacing.xs,
  },
  empty: {
    color:    Colors.textMuted,
    fontSize: FontSize.sm,
    padding:  Spacing.sm,
  },
});
