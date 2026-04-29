/**
 * Settings tab — server URL, connection status, password change, logout.
 */

import { useQuery } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import {
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
  Switch,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Colors, FontSize, Radius, Spacing } from '../../constants/theme';
import { apiClient } from '../../services/api';
import { useAppStore } from '../../stores/useAppStore';
import { useAuth } from '../../hooks/useAuth';

export default function SettingsScreen() {
  const { user, apiUrl, setApiUrl }   = useAppStore();
  const { logout }                    = useAuth();
  const [editingUrl, setEditingUrl]   = useState(false);
  const [urlDraft,   setUrlDraft]     = useState(apiUrl);
  const [changingPwd, setChangingPwd] = useState(false);
  const [newPwd,      setNewPwd]      = useState('');
  const [confirmPwd,  setConfirmPwd]  = useState('');

  const { data: status, refetch: checkStatus } = useQuery({
    queryKey: ['api-status'],
    queryFn:  () => apiClient.getStatus(),
    retry:    false,
  });

  const saveUrl = () => {
    const url = urlDraft.trim().replace(/\/$/, '');
    if (url) setApiUrl(url);
    setEditingUrl(false);
  };

  const handleChangePassword = async () => {
    if (newPwd.length < 8) {
      Alert.alert('Too Short', 'Password must be at least 8 characters.');
      return;
    }
    if (newPwd !== confirmPwd) {
      Alert.alert('Mismatch', 'Passwords do not match.');
      return;
    }
    try {
      await apiClient.changePassword(newPwd);
      Alert.alert('Success', 'Password changed successfully.');
      setChangingPwd(false);
      setNewPwd('');
      setConfirmPwd('');
    } catch (err: any) {
      Alert.alert('Error', err.message);
    }
  };

  const handleLogout = () => {
    Alert.alert('Sign Out', 'Are you sure you want to sign out?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign Out', style: 'destructive', onPress: logout },
    ]);
  };

  const statusColor = (s?: string) =>
    s === 'ok' ? Colors.ok : s === 'unreachable' ? Colors.critical : Colors.unknown;

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.title}>Settings</Text>

        {/* Account info */}
        <Section title="Account">
          <InfoRow label="Email"  value={user?.email ?? '—'} />
          <InfoRow label="Role"   value={(user?.role ?? '—').toUpperCase()} />
        </Section>

        {/* Server connection */}
        <Section title="Server Connection">
          {editingUrl ? (
            <View style={styles.urlEdit}>
              <TextInput
                style={styles.urlInput}
                value={urlDraft}
                onChangeText={setUrlDraft}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
                placeholder="http://YOUR_SERVER_IP:8000"
                placeholderTextColor={Colors.textDisabled}
              />
              <View style={styles.urlBtns}>
                <TouchableOpacity style={styles.saveBtn}   onPress={saveUrl}>
                  <Text style={styles.saveBtnText}>Save</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.cancelBtn} onPress={() => { setEditingUrl(false); setUrlDraft(apiUrl); }}>
                  <Text style={styles.cancelBtnText}>Cancel</Text>
                </TouchableOpacity>
              </View>
            </View>
          ) : (
            <TouchableOpacity onPress={() => { setEditingUrl(true); setUrlDraft(apiUrl); }}>
              <InfoRow label="API URL" value={apiUrl} />
            </TouchableOpacity>
          )}

          {/* Service status */}
          <View style={styles.statusRow}>
            {(['api', 'oracle', 'ollama'] as const).map((svc) => (
              <View key={svc} style={styles.statusChip}>
                <View style={[styles.statusDot, { backgroundColor: statusColor(status?.[svc]) }]} />
                <Text style={styles.statusLabel}>{svc.toUpperCase()}</Text>
              </View>
            ))}
            <TouchableOpacity onPress={() => checkStatus()} style={styles.refreshBtn}>
              <Ionicons name="refresh-outline" size={14} color={Colors.primary} />
              <Text style={styles.refreshText}>Check</Text>
            </TouchableOpacity>
          </View>
        </Section>

        {/* Change password */}
        <Section title="Security">
          {user?.mustChangePassword && (
            <View style={styles.mustChangeBanner}>
              <Ionicons name="warning-outline" size={14} color={Colors.black} />
              <Text style={styles.mustChangeText}>
                Your password is temporary — please change it.
              </Text>
            </View>
          )}

          {changingPwd ? (
            <View style={styles.pwdForm}>
              <TextInput
                style={styles.pwdInput}
                value={newPwd}
                onChangeText={setNewPwd}
                placeholder="New password (min 8 chars)"
                placeholderTextColor={Colors.textDisabled}
                secureTextEntry
              />
              <TextInput
                style={styles.pwdInput}
                value={confirmPwd}
                onChangeText={setConfirmPwd}
                placeholder="Confirm new password"
                placeholderTextColor={Colors.textDisabled}
                secureTextEntry
              />
              <View style={styles.urlBtns}>
                <TouchableOpacity style={styles.saveBtn} onPress={handleChangePassword}>
                  <Text style={styles.saveBtnText}>Update</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.cancelBtn}
                  onPress={() => { setChangingPwd(false); setNewPwd(''); setConfirmPwd(''); }}
                >
                  <Text style={styles.cancelBtnText}>Cancel</Text>
                </TouchableOpacity>
              </View>
            </View>
          ) : (
            <TouchableOpacity style={styles.actionRow} onPress={() => setChangingPwd(true)}>
              <Ionicons name="key-outline" size={18} color={Colors.text} />
              <Text style={styles.actionText}>Change Password</Text>
              <Ionicons name="chevron-forward" size={16} color={Colors.textMuted} />
            </TouchableOpacity>
          )}
        </Section>

        {/* Logout */}
        <Section title="">
          <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
            <Ionicons name="log-out-outline" size={20} color={Colors.critical} />
            <Text style={styles.logoutText}>Sign Out</Text>
          </TouchableOpacity>
        </Section>

        <Text style={styles.version}>OmniDBA Mobile v1.0.0</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={sectionStyles.container}>
      {title ? <Text style={sectionStyles.title}>{title}</Text> : null}
      <View style={sectionStyles.body}>{children}</View>
    </View>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={infoStyles.row}>
      <Text style={infoStyles.label}>{label}</Text>
      <Text style={infoStyles.value} numberOfLines={1}>{value}</Text>
    </View>
  );
}

const sectionStyles = StyleSheet.create({
  container: { marginBottom: Spacing.md },
  title: {
    color:        Colors.textMuted,
    fontSize:     FontSize.xs,
    fontWeight:   '600',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom:  Spacing.xs,
    paddingHorizontal: Spacing.xs,
  },
  body: {
    backgroundColor: Colors.surface,
    borderRadius:    Radius.md,
    borderWidth:     1,
    borderColor:     Colors.border,
    overflow:        'hidden',
  },
});

const infoStyles = StyleSheet.create({
  row: {
    flexDirection:   'row',
    justifyContent:  'space-between',
    alignItems:      'center',
    padding:          Spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  label: { color: Colors.textMuted, fontSize: FontSize.md },
  value: { color: Colors.text,      fontSize: FontSize.md, fontWeight: '500', flex: 1, textAlign: 'right' },
});

const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: Colors.background },
  scroll: { padding: Spacing.md, paddingBottom: Spacing.xl },
  title:  {
    color:        Colors.text,
    fontSize:     FontSize.xxl,
    fontWeight:   '700',
    marginBottom: Spacing.lg,
  },
  urlEdit:   { padding: Spacing.md, gap: Spacing.sm },
  urlInput:  {
    backgroundColor: Colors.surfaceRaised,
    borderWidth:     1,
    borderColor:     Colors.border,
    borderRadius:    Radius.sm,
    padding:         Spacing.sm,
    color:           Colors.text,
    fontSize:        FontSize.sm,
  },
  urlBtns:    { flexDirection: 'row', gap: Spacing.sm },
  saveBtn:    {
    flex:            1,
    backgroundColor: Colors.primary,
    borderRadius:    Radius.md,
    paddingVertical: Spacing.sm,
    alignItems:      'center',
  },
  saveBtnText:   { color: Colors.white, fontWeight: '700' },
  cancelBtn:     {
    flex:            1,
    backgroundColor: Colors.surfaceRaised,
    borderRadius:    Radius.md,
    paddingVertical: Spacing.sm,
    alignItems:      'center',
    borderWidth:     1,
    borderColor:     Colors.border,
  },
  cancelBtnText: { color: Colors.textMuted, fontWeight: '600' },
  statusRow: {
    flexDirection:  'row',
    alignItems:     'center',
    padding:         Spacing.md,
    gap:             Spacing.sm,
  },
  statusChip:  { flexDirection: 'row', alignItems: 'center', gap: 4 },
  statusDot:   { width: 8, height: 8, borderRadius: 4 },
  statusLabel: { color: Colors.textMuted, fontSize: FontSize.xs, fontWeight: '600' },
  refreshBtn:  { flexDirection: 'row', alignItems: 'center', gap: 2, marginLeft: 'auto' as any },
  refreshText: { color: Colors.primary, fontSize: FontSize.xs },
  mustChangeBanner: {
    flexDirection:   'row',
    alignItems:      'center',
    backgroundColor: Colors.warning,
    padding:          Spacing.sm,
    gap:              Spacing.xs,
    margin:           Spacing.sm,
    borderRadius:     Radius.sm,
  },
  mustChangeText: { color: Colors.black, fontSize: FontSize.xs, flex: 1 },
  pwdForm: { padding: Spacing.md, gap: Spacing.sm },
  pwdInput: {
    backgroundColor: Colors.surfaceRaised,
    borderWidth:     1,
    borderColor:     Colors.border,
    borderRadius:    Radius.sm,
    padding:         Spacing.sm,
    color:           Colors.text,
    fontSize:        FontSize.md,
  },
  actionRow: {
    flexDirection:  'row',
    alignItems:     'center',
    padding:         Spacing.md,
    gap:             Spacing.sm,
  },
  actionText: { color: Colors.text, fontSize: FontSize.md, flex: 1 },
  logoutBtn: {
    flexDirection:  'row',
    alignItems:     'center',
    justifyContent: 'center',
    padding:         Spacing.md,
    gap:             Spacing.sm,
  },
  logoutText: { color: Colors.critical, fontSize: FontSize.md, fontWeight: '600' },
  version: {
    color:     Colors.textDisabled,
    fontSize:  FontSize.xs,
    textAlign: 'center',
    marginTop: Spacing.lg,
  },
});
