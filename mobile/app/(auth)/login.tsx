import { useRouter } from 'expo-router';
import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '../../hooks/useAuth';
import { Colors, FontSize, Radius, Spacing } from '../../constants/theme';

export default function LoginScreen() {
  const router                  = useRouter();
  const { loginWithPassword }   = useAuth();

  const [email,    setEmail]    = useState('');
  const [password, setPassword] = useState('');
  const [loading,  setLoading]  = useState(false);

  const handleLogin = async () => {
    const e = email.trim();
    const p = password.trim();
    if (!e || !p) {
      Alert.alert('Required', 'Please enter your email and password.');
      return;
    }
    setLoading(true);
    try {
      const data = await loginWithPassword(e, p);
      if (data.must_change_password) {
        Alert.alert(
          'Change Password Required',
          'Your password is temporary. Please change it in Settings after logging in.',
        );
      }
      router.replace('/(tabs)/');
    } catch (err: any) {
      Alert.alert('Login Failed', err?.message ?? 'Invalid email or password');
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.flex}
      >
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          {/* Logo / branding */}
          <View style={styles.header}>
            <View style={styles.logoCircle}>
              <Text style={styles.logoText}>DB</Text>
            </View>
            <Text style={styles.appName}>OmniDBA</Text>
            <Text style={styles.appSubtitle}>Autonomous DBA Assistant</Text>
          </View>

          {/* Form card */}
          <View style={styles.card}>
            <Text style={styles.formTitle}>Sign in</Text>

            <Text style={styles.label}>Email</Text>
            <TextInput
              style={styles.input}
              value={email}
              onChangeText={setEmail}
              placeholder="you@cgi.com"
              placeholderTextColor={Colors.textDisabled}
              autoCapitalize="none"
              keyboardType="email-address"
              textContentType="emailAddress"
              autoComplete="email"
              returnKeyType="next"
              editable={!loading}
            />

            <Text style={styles.label}>Password</Text>
            <TextInput
              style={styles.input}
              value={password}
              onChangeText={setPassword}
              placeholder="Password"
              placeholderTextColor={Colors.textDisabled}
              secureTextEntry
              textContentType="password"
              autoComplete="current-password"
              returnKeyType="done"
              onSubmitEditing={handleLogin}
              editable={!loading}
            />

            <TouchableOpacity
              style={[styles.loginBtn, loading && styles.btnDisabled]}
              onPress={handleLogin}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color={Colors.white} />
              ) : (
                <Text style={styles.loginBtnText}>Sign In</Text>
              )}
            </TouchableOpacity>

            <Text style={styles.hint}>
              Default password: Test123{'\n'}
              Change it in Settings after first login.
            </Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:  { flex: 1, backgroundColor: Colors.background },
  flex:  { flex: 1 },
  scroll: {
    flexGrow:        1,
    justifyContent:  'center',
    paddingHorizontal: Spacing.lg,
    paddingVertical:   Spacing.xl,
  },
  header: {
    alignItems:    'center',
    marginBottom:  Spacing.xl,
  },
  logoCircle: {
    width:           72,
    height:          72,
    borderRadius:    36,
    backgroundColor: Colors.primary,
    alignItems:      'center',
    justifyContent:  'center',
    marginBottom:    Spacing.md,
  },
  logoText:     { color: Colors.white, fontSize: 28, fontWeight: '700' },
  appName:      { color: Colors.text,  fontSize: FontSize.xxl, fontWeight: '700' },
  appSubtitle:  { color: Colors.textMuted, fontSize: FontSize.sm, marginTop: 4 },
  card: {
    backgroundColor: Colors.surface,
    borderRadius:    Radius.lg,
    padding:         Spacing.lg,
    borderWidth:     1,
    borderColor:     Colors.border,
  },
  formTitle: {
    color:        Colors.text,
    fontSize:     FontSize.xl,
    fontWeight:   '700',
    marginBottom: Spacing.lg,
  },
  label: {
    color:        Colors.textMuted,
    fontSize:     FontSize.sm,
    marginBottom: Spacing.xs,
    fontWeight:   '500',
  },
  input: {
    backgroundColor: Colors.surfaceRaised,
    borderWidth:     1,
    borderColor:     Colors.border,
    borderRadius:    Radius.sm,
    padding:         Spacing.md,
    color:           Colors.text,
    fontSize:        FontSize.md,
    marginBottom:    Spacing.md,
  },
  loginBtn: {
    backgroundColor: Colors.primary,
    borderRadius:    Radius.md,
    paddingVertical: Spacing.md,
    alignItems:      'center',
    marginTop:       Spacing.sm,
  },
  btnDisabled:   { opacity: 0.6 },
  loginBtnText:  { color: Colors.white, fontWeight: '700', fontSize: FontSize.md },
  hint: {
    color:     Colors.textDisabled,
    fontSize:  FontSize.xs,
    textAlign: 'center',
    marginTop: Spacing.lg,
    lineHeight: FontSize.xs * 1.6,
  },
});
