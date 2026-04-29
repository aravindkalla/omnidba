/**
 * HitlApprovalCard — shows the RMAN script + structured backup params,
 * with Approve / Reject / Revise actions.
 */

import React, { useState } from 'react';
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Colors, FontSize, Radius, Spacing } from '../constants/theme';

interface BackupParams {
  backup_type:      string;
  level:            number;
  compress:         boolean;
  encrypt:          boolean;
  channels:         number;
  destination?:     string;
  delete_input:     boolean;
  delete_obsolete:  boolean;
  tag?:             string;
}

interface Props {
  message:       string;
  rmanScript:    string;
  backupParams:  BackupParams;
  onApprove:     () => Promise<void>;
  onReject:      () => Promise<void>;
  onRevise:      (feedback: string) => Promise<void>;
  loading:       boolean;
}

export function HitlApprovalCard({
  message,
  rmanScript,
  backupParams,
  onApprove,
  onReject,
  onRevise,
  loading,
}: Props) {
  const [reviseMode, setReviseMode] = useState(false);
  const [feedback,   setFeedback]   = useState('');

  return (
    <View style={styles.card}>
      {/* Header */}
      <View style={styles.warningBanner}>
        <Text style={styles.warningText}>RMAN Backup Requires Approval</Text>
      </View>

      <Text style={styles.message}>{message}</Text>

      {/* Backup params summary */}
      <View style={styles.paramsGrid}>
        <ParamRow label="Type"     value={backupParams.backup_type.toUpperCase()} />
        <ParamRow label="Channels" value={String(backupParams.channels)} />
        <ParamRow label="Compress" value={backupParams.compress ? 'Yes' : 'No'} />
        <ParamRow label="Encrypt"  value={backupParams.encrypt  ? 'Yes' : 'No'} />
        {backupParams.destination ? (
          <ParamRow label="Dest" value={backupParams.destination} />
        ) : null}
        <ParamRow label="Delete Input"    value={backupParams.delete_input    ? 'Yes' : 'No'} />
        <ParamRow label="Delete Obsolete" value={backupParams.delete_obsolete ? 'Yes' : 'No'} />
      </View>

      {/* RMAN script */}
      <Text style={styles.sectionTitle}>RMAN Script</Text>
      <ScrollView style={styles.scriptBox} horizontal={false}>
        <Text style={styles.scriptText}>{rmanScript}</Text>
      </ScrollView>

      {/* Revision input */}
      {reviseMode && (
        <View style={styles.reviseBox}>
          <Text style={styles.sectionTitle}>Revision Feedback</Text>
          <TextInput
            style={styles.reviseInput}
            value={feedback}
            onChangeText={setFeedback}
            placeholder="e.g. Use 4 channels instead of 2, add encrypt"
            placeholderTextColor={Colors.textDisabled}
            multiline
            numberOfLines={3}
            autoFocus
          />
        </View>
      )}

      {/* Action buttons */}
      {loading ? (
        <ActivityIndicator color={Colors.primary} style={styles.loader} />
      ) : (
        <View style={styles.actions}>
          {!reviseMode ? (
            <>
              <TouchableOpacity
                style={[styles.btn, styles.btnApprove]}
                onPress={onApprove}
              >
                <Text style={styles.btnText}>Approve</Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[styles.btn, styles.btnRevise]}
                onPress={() => setReviseMode(true)}
              >
                <Text style={styles.btnText}>Revise</Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[styles.btn, styles.btnReject]}
                onPress={onReject}
              >
                <Text style={styles.btnText}>Reject</Text>
              </TouchableOpacity>
            </>
          ) : (
            <>
              <TouchableOpacity
                style={[styles.btn, styles.btnApprove, !feedback.trim() && styles.btnDisabled]}
                onPress={() => {
                  if (feedback.trim()) {
                    onRevise(feedback);
                    setReviseMode(false);
                    setFeedback('');
                  }
                }}
                disabled={!feedback.trim()}
              >
                <Text style={styles.btnText}>Submit Revision</Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[styles.btn, styles.btnRevise]}
                onPress={() => { setReviseMode(false); setFeedback(''); }}
              >
                <Text style={styles.btnText}>Cancel</Text>
              </TouchableOpacity>
            </>
          )}
        </View>
      )}
    </View>
  );
}

function ParamRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={paramStyles.row}>
      <Text style={paramStyles.label}>{label}</Text>
      <Text style={paramStyles.value}>{value}</Text>
    </View>
  );
}

const paramStyles = StyleSheet.create({
  row:   { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3 },
  label: { color: Colors.textMuted, fontSize: FontSize.sm },
  value: { color: Colors.text,      fontSize: FontSize.sm, fontWeight: '600' },
});

const styles = StyleSheet.create({
  card: {
    backgroundColor: Colors.surface,
    borderRadius:    Radius.md,
    borderWidth:     1,
    borderColor:     Colors.warning,
    overflow:        'hidden',
  },
  warningBanner: {
    backgroundColor: Colors.warning,
    paddingVertical:   Spacing.sm,
    paddingHorizontal: Spacing.md,
  },
  warningText: {
    color:      Colors.black,
    fontWeight: '700',
    fontSize:   FontSize.md,
  },
  message: {
    color:   Colors.text,
    padding: Spacing.md,
    fontSize: FontSize.md,
  },
  paramsGrid: {
    marginHorizontal: Spacing.md,
    marginBottom:     Spacing.sm,
    backgroundColor:  Colors.surfaceRaised,
    padding:          Spacing.sm,
    borderRadius:     Radius.sm,
  },
  sectionTitle: {
    color:           Colors.textMuted,
    fontSize:        FontSize.sm,
    fontWeight:      '600',
    marginHorizontal: Spacing.md,
    marginBottom:    Spacing.xs,
  },
  scriptBox: {
    backgroundColor: '#0D1117',
    marginHorizontal: Spacing.md,
    borderRadius:    Radius.sm,
    padding:         Spacing.sm,
    maxHeight:       200,
    marginBottom:    Spacing.md,
  },
  scriptText: {
    color:      '#7EE787',  // terminal green
    fontFamily: 'monospace',
    fontSize:   FontSize.xs,
    lineHeight: FontSize.xs * 1.6,
  },
  reviseBox: {
    marginHorizontal: Spacing.md,
    marginBottom:     Spacing.sm,
  },
  reviseInput: {
    backgroundColor:  Colors.surfaceRaised,
    borderWidth:      1,
    borderColor:      Colors.border,
    borderRadius:     Radius.sm,
    padding:          Spacing.sm,
    color:            Colors.text,
    fontSize:         FontSize.md,
    textAlignVertical: 'top',
  },
  actions: {
    flexDirection:  'row',
    padding:        Spacing.md,
    gap:            Spacing.sm,
  },
  btn: {
    flex:            1,
    paddingVertical: Spacing.sm,
    borderRadius:    Radius.md,
    alignItems:      'center',
  },
  btnApprove: { backgroundColor: Colors.ok },
  btnRevise:  { backgroundColor: Colors.primary },
  btnReject:  { backgroundColor: Colors.critical },
  btnDisabled:{ opacity: 0.4 },
  btnText: {
    color:      Colors.white,
    fontWeight: '700',
    fontSize:   FontSize.sm,
  },
  loader: { padding: Spacing.lg },
});
