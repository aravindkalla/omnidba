/**
 * Health Dashboard tab — runs GET /health-report and displays all 7 Oracle
 * metrics as cards + bar charts.  Pull-to-refresh re-fetches everything.
 */

import { useQuery } from '@tanstack/react-query';
import React from 'react';
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { HorizontalBarChart, BarEntry } from '../../components/HorizontalBarChart';
import { MetricCard } from '../../components/MetricCard';
import { Colors, FontSize, Spacing } from '../../constants/theme';
import { apiClient, HealthReport, MetricResult } from '../../services/api';

// ── Data adapters: MetricResult → props for our chart/card components ─────────

function tablespaceToChart(m: MetricResult): BarEntry[] {
  // columns: TABLESPACE_NAME … PCT_USED
  const nameIdx = m.columns.findIndex((c) => c.includes('NAME'));
  const pctIdx  = m.columns.findIndex((c) => c.includes('PCT'));
  if (nameIdx === -1 || pctIdx === -1) return [];
  return m.rows.map((r) => ({
    label: String(r[nameIdx]),
    value: Number(r[pctIdx] ?? 0),
  }));
}

function topSqlToChart(m: MetricResult): BarEntry[] {
  // columns: SQL_ID … AVG_ELAPSED_SEC
  const idIdx  = m.columns.findIndex((c) => c.includes('SQL_ID'));
  const secIdx = m.columns.findIndex((c) => c.includes('AVG_ELAPSED'));
  if (idIdx === -1 || secIdx === -1) return [];
  return m.rows.map((r) => ({
    label: String(r[idIdx]).slice(0, 10),
    value: Number(r[secIdx] ?? 0),
  }));
}

export default function HealthScreen() {
  const {
    data,
    isLoading,
    isRefetching,
    refetch,
    error,
  } = useQuery<HealthReport>({
    queryKey: ['health-report'],
    queryFn:  () => apiClient.getHealthReport(),
    staleTime: 60_000,
  });

  const overallColor =
    data?.overall === 'critical' ? Colors.critical :
    data?.overall === 'warning'  ? Colors.warning  :
    Colors.ok;

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={refetch}
            tintColor={Colors.primary}
          />
        }
      >
        {/* Page header */}
        <View style={styles.header}>
          <Text style={styles.title}>DB Health Report</Text>
          {data && (
            <View style={[styles.overallBadge, { backgroundColor: overallColor + '22' }]}>
              <Text style={[styles.overallText, { color: overallColor }]}>
                {data.overall.toUpperCase()}
              </Text>
            </View>
          )}
        </View>

        {data?.generated_at && (
          <Text style={styles.timestamp}>
            Last updated: {new Date(data.generated_at).toLocaleString()}
          </Text>
        )}

        {/* Loading state */}
        {isLoading && (
          <View style={styles.center}>
            <ActivityIndicator size="large" color={Colors.primary} />
            <Text style={styles.loadingText}>Fetching Oracle metrics…</Text>
          </View>
        )}

        {/* Error state */}
        {error && !isLoading && (
          <View style={styles.center}>
            <Text style={styles.errorText}>
              {(error as Error).message ?? 'Failed to load health report'}
            </Text>
          </View>
        )}

        {data && (
          <>
            {/* ── Row 1: Number metric cards ───────────────────────────────── */}
            <Text style={styles.sectionTitle}>Session Overview</Text>
            <View style={styles.cardRow}>
              <MetricCard
                title    ="Active Sessions"
                value    ={data.metrics.active_sessions?.rows?.length ?? 0}
                subtitle ="connected users"
                status   ={data.metrics.active_sessions?.status ?? 'unknown'}
              />
              <MetricCard
                title    ="Blocking Sessions"
                value    ={data.metrics.blocking_sessions?.rows?.length ?? 0}
                subtitle ="lock contentions"
                status   ={data.metrics.blocking_sessions?.status ?? 'unknown'}
              />
            </View>

            <View style={styles.cardRow}>
              <MetricCard
                title    ="Invalid Objects"
                value    ={data.metrics.invalid_objects?.rows?.length ?? 0}
                subtitle ="compile failures"
                status   ={data.metrics.invalid_objects?.status ?? 'unknown'}
              />
              <MetricCard
                title    ="Buffer Cache Hit"
                value    ={
                  data.metrics.buffer_cache_hit_ratio?.rows?.[0]?.[0] != null
                    ? `${Number(data.metrics.buffer_cache_hit_ratio.rows[0][0]).toFixed(1)}%`
                    : '—'
                }
                subtitle ="hit ratio"
                status   ={data.metrics.buffer_cache_hit_ratio?.status ?? 'unknown'}
              />
            </View>

            {/* ── Tablespace usage bar chart ────────────────────────────────── */}
            <Text style={styles.sectionTitle}>Tablespace Usage</Text>
            <HorizontalBarChart
              title    ="Usage by Tablespace (% used)"
              data     ={tablespaceToChart(data.metrics.tablespace_usage ?? { columns: [], rows: [], status: 'unknown', error: null })}
              maxValue ={100}
              unit     ="%"
              status   ={data.metrics.tablespace_usage?.status ?? 'unknown'}
            />

            {/* ── Top SQL bar chart ─────────────────────────────────────────── */}
            <Text style={styles.sectionTitle}>Top SQL Performance</Text>
            <HorizontalBarChart
              title    ="Avg Elapsed Time by SQL ID (seconds)"
              data     ={topSqlToChart(data.metrics.top_sql ?? { columns: [], rows: [], status: 'unknown', error: null })}
              unit     ="s"
              status   ={data.metrics.top_sql?.status ?? 'ok'}
            />

            {/* ── Redo switches ─────────────────────────────────────────────── */}
            <Text style={styles.sectionTitle}>Redo Log Switches (last 24h)</Text>
            <HorizontalBarChart
              title    ="Switches per Hour"
              data     ={(data.metrics.redo_switches_24h?.rows ?? []).map((r) => ({
                label: String(r[0]).slice(-5),  // last 5 chars: "HH:00"
                value: Number(r[1] ?? 0),
              }))}
              unit     =""
              status   ={data.metrics.redo_switches_24h?.status ?? 'ok'}
            />
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: Colors.background },
  scroll: { padding: Spacing.md, paddingBottom: Spacing.xl },
  header: {
    flexDirection:  'row',
    alignItems:     'center',
    justifyContent: 'space-between',
    marginBottom:   Spacing.xs,
  },
  title: {
    color:      Colors.text,
    fontSize:   FontSize.xl,
    fontWeight: '700',
  },
  overallBadge: {
    paddingVertical:   Spacing.xs,
    paddingHorizontal: Spacing.sm,
    borderRadius:      999,
  },
  overallText: { fontSize: FontSize.sm, fontWeight: '700' },
  timestamp: {
    color:        Colors.textDisabled,
    fontSize:     FontSize.xs,
    marginBottom: Spacing.md,
  },
  sectionTitle: {
    color:        Colors.textMuted,
    fontSize:     FontSize.sm,
    fontWeight:   '600',
    marginTop:    Spacing.lg,
    marginBottom: Spacing.xs,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  cardRow: {
    flexDirection: 'row',
    marginHorizontal: -Spacing.xs,
  },
  center: {
    alignItems:     'center',
    justifyContent: 'center',
    paddingVertical: Spacing.xl,
    gap: Spacing.md,
  },
  loadingText: { color: Colors.textMuted, fontSize: FontSize.md },
  errorText:   { color: Colors.critical,  fontSize: FontSize.md, textAlign: 'center' },
});
