/**
 * HITL Approval screen — opened by tapping the push notification.
 * Deep-link: oracledba://hitl-approval?thread_id=xxx
 *
 * Shows the RMAN script + structured params and lets the DBA:
 *   Approve → POST /approve with response="approve"
 *   Reject  → POST /approve with response="reject"
 *   Revise  → POST /approve with revision text → may trigger a second interrupt
 */

import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { HitlApprovalCard } from '../components/HitlApprovalCard';
import { Colors, FontSize, Spacing } from '../constants/theme';
import { apiClient } from '../services/api';
import { useAppStore } from '../stores/useAppStore';

export default function HitlApprovalScreen() {
  const router                    = useRouter();
  const params                    = useLocalSearchParams<{ thread_id: string }>();
  const { pendingHitl, setPendingHitl } = useAppStore();

  const threadId                  = params.thread_id ?? pendingHitl?.threadId ?? '';

  // The HITL payload may arrive via push notification data (in store) or
  // we might need to query the server.  For now we use what's in the store.
  const [payload,  setPayload]    = useState(pendingHitl?.payload ?? null);
  const [loading,  setLoading]    = useState(!payload);
  const [done,     setDone]       = useState(false);
  const [result,   setResult]     = useState<string | null>(null);

  // If payload wasn't in the store (e.g. app was closed), show a waiting message.
  // In a future enhancement we'd poll GET /query/{thread_id}/status here.
  useEffect(() => {
    if (!payload) {
      setLoading(false);
    }
  }, [payload]);

  const handleDecision = useCallback(
    async (response: string) => {
      if (!threadId) return;
      setLoading(true);
      try {
        const resp = await apiClient.approve(threadId, response);

        if (resp.hitl_pending && resp.hitl_payload) {
          // Revised script needs another round of approval
          setPayload(resp.hitl_payload as any);
          setLoading(false);
        } else {
          setResult(resp.final_result ?? 'Operation completed.');
          setDone(true);
          setPendingHitl(null);
          setLoading(false);
        }
      } catch (err: any) {
        Alert.alert('Error', err.message);
        setLoading(false);
      }
    },
    [threadId, setPendingHitl],
  );

  if (done) {
    return (
      <SafeAreaView style={styles.safe} edges={['bottom']}>
        <View style={styles.doneCenter}>
          <Text style={[
            styles.doneIcon,
            result?.includes('SUCCEEDED') ? styles.successIcon : styles.neutralIcon,
          ]}>
            {result?.includes('SUCCEEDED') ? '✓' : result?.includes('abort') ? '✗' : '✓'}
          </Text>
          <Text style={styles.doneTitle}>
            {result?.includes('SUCCEEDED') ? 'Backup Completed' :
             result?.includes('abort')     ? 'Backup Aborted'  : 'Done'}
          </Text>
          <Text style={styles.doneResult}>{result}</Text>
          <Text style={styles.doneTap} onPress={() => router.back()}>
            Tap to close
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  if (!payload && !loading) {
    return (
      <SafeAreaView style={styles.safe} edges={['bottom']}>
        <View style={styles.doneCenter}>
          <Text style={styles.noPayloadText}>
            Backup approval request not found.{'\n'}
            It may have already been processed.
          </Text>
          <Text style={styles.doneTap} onPress={() => router.back()}>
            Go back
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        {!payload ? (
          <View style={styles.doneCenter}>
            <ActivityIndicator color={Colors.primary} size="large" />
            <Text style={styles.loadingText}>Loading approval details…</Text>
          </View>
        ) : (
          <>
            <Text style={styles.threadId}>Thread: {threadId.slice(0, 8)}…</Text>
            <HitlApprovalCard
              message      ={payload.message}
              rmanScript   ={payload.rman_script}
              backupParams ={payload.backup_params as any}
              onApprove    ={() => handleDecision('approve')}
              onReject     ={() => handleDecision('reject')}
              onRevise     ={(feedback) => handleDecision(feedback)}
              loading      ={loading}
            />
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:          { flex: 1, backgroundColor: Colors.background },
  scroll:        { padding: Spacing.md, paddingBottom: Spacing.xl },
  threadId:      { color: Colors.textDisabled, fontSize: FontSize.xs, marginBottom: Spacing.sm },
  doneCenter:    { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.xl },
  doneIcon:      { fontSize: 64, marginBottom: Spacing.md },
  successIcon:   { color: Colors.ok },
  neutralIcon:   { color: Colors.critical },
  doneTitle:     { color: Colors.text, fontSize: FontSize.xxl, fontWeight: '700', marginBottom: Spacing.sm },
  doneResult:    { color: Colors.textMuted, fontSize: FontSize.md, textAlign: 'center', lineHeight: FontSize.md * 1.6 },
  doneTap:       { color: Colors.primary, fontSize: FontSize.md, marginTop: Spacing.xl },
  noPayloadText: { color: Colors.textMuted, fontSize: FontSize.md, textAlign: 'center', lineHeight: FontSize.md * 1.6 },
  loadingText:   { color: Colors.textMuted, fontSize: FontSize.md, marginTop: Spacing.md },
});
