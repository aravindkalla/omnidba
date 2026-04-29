/**
 * Schedules tab — create and manage recurring RMAN backup jobs.
 * Scheduled jobs still require HITL approval when they fire — no auto-execution.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Colors, FontSize, Radius, Spacing } from '../../constants/theme';
import { apiClient, ScheduledJob } from '../../services/api';
import { useAppStore } from '../../stores/useAppStore';

// Common cron presets for quick selection
const PRESETS = [
  { label: 'Daily at 2 AM',    cron: '0 2 * * *' },
  { label: 'Daily at midnight', cron: '0 0 * * *' },
  { label: 'Weekly (Sun 2 AM)', cron: '0 2 * * 0' },
  { label: 'Every 6 hours',    cron: '0 */6 * * *' },
];

export default function SchedulesScreen() {
  const { user }   = useAppStore();
  const qc         = useQueryClient();
  const [showForm, setShowForm] = useState(false);

  const { data: jobs = [], isLoading, isRefetching, refetch } = useQuery<ScheduledJob[]>({
    queryKey: ['schedules'],
    queryFn:  () => apiClient.getSchedules(),
  });

  const deleteMutation = useMutation({
    mutationFn: (jobId: string) => apiClient.deleteSchedule(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });

  const confirmDelete = (job: ScheduledJob) => {
    Alert.alert(
      'Cancel Schedule',
      `Cancel "${job.name}"?  This cannot be undone.`,
      [
        { text: 'Keep it',  style: 'cancel' },
        {
          text:    'Cancel Schedule',
          style:   'destructive',
          onPress: () => deleteMutation.mutate(job.job_id),
        },
      ],
    );
  };

  const renderJob = ({ item }: { item: ScheduledJob }) => (
    <View style={styles.jobCard}>
      <View style={styles.jobHeader}>
        <Text style={styles.jobName}>{item.name}</Text>
        <TouchableOpacity
          onPress={() => confirmDelete(item)}
          style={styles.deleteBtn}
          disabled={deleteMutation.isPending}
        >
          <Ionicons name="trash-outline" size={16} color={Colors.critical} />
        </TouchableOpacity>
      </View>

      <View style={styles.cronRow}>
        <Ionicons name="time-outline" size={13} color={Colors.textMuted} />
        <Text style={styles.cronText}>{item.cron_expr}</Text>
        <View style={[styles.dot, item.enabled ? styles.dotOn : styles.dotOff]} />
      </View>

      <Text style={styles.queryText} numberOfLines={2}>{item.backup_query}</Text>

      <View style={styles.meta}>
        <Text style={styles.metaText}>by {item.created_by.split('@')[0]}</Text>
        {item.last_fired && (
          <Text style={styles.metaText}>
            last: {new Date(item.last_fired).toLocaleDateString()}
          </Text>
        )}
      </View>
    </View>
  );

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      {/* Header bar */}
      <View style={styles.header}>
        <Text style={styles.title}>Backup Schedules</Text>
        <TouchableOpacity style={styles.addBtn} onPress={() => setShowForm(true)}>
          <Ionicons name="add" size={20} color={Colors.white} />
          <Text style={styles.addBtnText}>New</Text>
        </TouchableOpacity>
      </View>

      <Text style={styles.note}>
        Scheduled backups send push notifications for HITL approval — they never run automatically.
      </Text>

      {isLoading ? (
        <ActivityIndicator color={Colors.primary} style={styles.loader} />
      ) : (
        <FlatList
          data={jobs}
          keyExtractor={(j) => j.job_id}
          renderItem={renderJob}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={Colors.primary} />
          }
          ListEmptyComponent={
            <View style={styles.empty}>
              <Ionicons name="calendar-outline" size={48} color={Colors.textDisabled} />
              <Text style={styles.emptyText}>No scheduled backups</Text>
              <Text style={styles.emptyHint}>Tap "New" to create a recurring backup schedule</Text>
            </View>
          }
        />
      )}

      {/* Create schedule modal */}
      <CreateScheduleModal
        visible  ={showForm}
        onClose  ={() => setShowForm(false)}
        onCreated={() => {
          setShowForm(false);
          qc.invalidateQueries({ queryKey: ['schedules'] });
        }}
      />
    </SafeAreaView>
  );
}

// ── Create Schedule Modal ─────────────────────────────────────────────────────

function CreateScheduleModal({
  visible,
  onClose,
  onCreated,
}: {
  visible: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name,    setName]    = useState('');
  const [cron,    setCron]    = useState('0 2 * * *');
  const [query,   setQuery]   = useState('Run a full compressed database backup');

  const mutation = useMutation({
    mutationFn: () => apiClient.createSchedule({ name, cron_expr: cron, backup_query: query }),
    onSuccess: () => {
      onCreated();
      setName('');
      setCron('0 2 * * *');
      setQuery('Run a full compressed database backup');
    },
    onError: (err: any) => Alert.alert('Error', err.message),
  });

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet">
      <SafeAreaView style={styles.modalSafe}>
        <View style={styles.modalHeader}>
          <Text style={styles.modalTitle}>New Scheduled Backup</Text>
          <TouchableOpacity onPress={onClose}>
            <Ionicons name="close" size={24} color={Colors.textMuted} />
          </TouchableOpacity>
        </View>

        <View style={styles.modalBody}>
          <Text style={styles.fieldLabel}>Schedule Name</Text>
          <TextInput
            style={styles.fieldInput}
            value={name}
            onChangeText={setName}
            placeholder="e.g. Nightly Full Backup"
            placeholderTextColor={Colors.textDisabled}
          />

          <Text style={styles.fieldLabel}>Cron Expression (UTC)</Text>
          <TextInput
            style={styles.fieldInput}
            value={cron}
            onChangeText={setCron}
            placeholder="0 2 * * *"
            placeholderTextColor={Colors.textDisabled}
            autoCapitalize="none"
          />

          {/* Presets */}
          <View style={styles.presets}>
            {PRESETS.map((p) => (
              <TouchableOpacity
                key={p.cron}
                style={[styles.presetChip, cron === p.cron && styles.presetChipActive]}
                onPress={() => setCron(p.cron)}
              >
                <Text style={[styles.presetText, cron === p.cron && styles.presetTextActive]}>
                  {p.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          <Text style={styles.fieldLabel}>Backup Query</Text>
          <TextInput
            style={[styles.fieldInput, styles.fieldInputMulti]}
            value={query}
            onChangeText={setQuery}
            placeholder="Run a full compressed database backup"
            placeholderTextColor={Colors.textDisabled}
            multiline
            numberOfLines={3}
            textAlignVertical="top"
          />

          <TouchableOpacity
            style={[styles.createBtn, (!name || !cron || !query || mutation.isPending) && styles.btnDisabled]}
            onPress={() => mutation.mutate()}
            disabled={!name || !cron || !query || mutation.isPending}
          >
            {mutation.isPending
              ? <ActivityIndicator color={Colors.white} />
              : <Text style={styles.createBtnText}>Create Schedule</Text>
            }
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe:    { flex: 1, backgroundColor: Colors.background },
  header:  {
    flexDirection:   'row',
    alignItems:      'center',
    justifyContent:  'space-between',
    padding:          Spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
    backgroundColor:   Colors.surface,
  },
  title:   { color: Colors.text,  fontSize: FontSize.xl, fontWeight: '700' },
  addBtn:  {
    flexDirection:   'row',
    alignItems:      'center',
    backgroundColor: Colors.primary,
    paddingVertical: Spacing.xs,
    paddingHorizontal: Spacing.sm,
    borderRadius:    Radius.md,
    gap: 4,
  },
  addBtnText: { color: Colors.white, fontWeight: '700', fontSize: FontSize.sm },
  note:  {
    color:      Colors.textMuted,
    fontSize:   FontSize.xs,
    padding:    Spacing.md,
    paddingBottom: 0,
  },
  loader: { marginTop: Spacing.xl },
  list:   { padding: Spacing.md, paddingBottom: Spacing.xl },
  jobCard: {
    backgroundColor: Colors.surface,
    borderRadius:    Radius.md,
    borderWidth:     1,
    borderColor:     Colors.border,
    padding:         Spacing.md,
    marginBottom:    Spacing.sm,
  },
  jobHeader: {
    flexDirection:  'row',
    justifyContent: 'space-between',
    alignItems:     'flex-start',
    marginBottom:   Spacing.xs,
  },
  jobName:    { color: Colors.text, fontSize: FontSize.md, fontWeight: '600', flex: 1 },
  deleteBtn:  { padding: Spacing.xs },
  cronRow:    { flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: Spacing.xs },
  cronText:   { color: Colors.primary, fontSize: FontSize.sm, fontFamily: 'monospace', flex: 1 },
  dot:        { width: 8, height: 8, borderRadius: 4 },
  dotOn:      { backgroundColor: Colors.ok },
  dotOff:     { backgroundColor: Colors.critical },
  queryText:  { color: Colors.textMuted, fontSize: FontSize.sm, marginBottom: Spacing.xs },
  meta:       { flexDirection: 'row', justifyContent: 'space-between' },
  metaText:   { color: Colors.textDisabled, fontSize: FontSize.xs },
  empty:      { alignItems: 'center', paddingTop: Spacing.xxl, gap: Spacing.sm },
  emptyText:  { color: Colors.textMuted, fontSize: FontSize.lg, fontWeight: '600' },
  emptyHint:  { color: Colors.textDisabled, fontSize: FontSize.sm, textAlign: 'center' },

  // Modal
  modalSafe:    { flex: 1, backgroundColor: Colors.background },
  modalHeader:  {
    flexDirection:   'row',
    justifyContent:  'space-between',
    alignItems:      'center',
    padding:          Spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
    backgroundColor:   Colors.surface,
  },
  modalTitle: { color: Colors.text, fontSize: FontSize.lg, fontWeight: '700' },
  modalBody:  { padding: Spacing.md, gap: Spacing.sm },
  fieldLabel: { color: Colors.textMuted, fontSize: FontSize.sm, fontWeight: '600' },
  fieldInput: {
    backgroundColor: Colors.surface,
    borderWidth:     1,
    borderColor:     Colors.border,
    borderRadius:    Radius.sm,
    padding:         Spacing.sm,
    color:           Colors.text,
    fontSize:        FontSize.md,
  },
  fieldInputMulti: { minHeight: 72, textAlignVertical: 'top' },
  presets: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.xs },
  presetChip: {
    paddingVertical:   Spacing.xs,
    paddingHorizontal: Spacing.sm,
    borderRadius:      Radius.full,
    borderWidth:       1,
    borderColor:       Colors.border,
    backgroundColor:   Colors.surface,
  },
  presetChipActive:  { borderColor: Colors.primary, backgroundColor: Colors.primary + '22' },
  presetText:        { color: Colors.textMuted, fontSize: FontSize.xs },
  presetTextActive:  { color: Colors.primary },
  createBtn: {
    backgroundColor: Colors.primary,
    borderRadius:    Radius.md,
    paddingVertical: Spacing.md,
    alignItems:      'center',
    marginTop:       Spacing.sm,
  },
  btnDisabled:    { opacity: 0.4 },
  createBtnText:  { color: Colors.white, fontWeight: '700', fontSize: FontSize.md },
});
