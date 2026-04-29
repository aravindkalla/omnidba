/**
 * Chat tab — natural language DBA queries, identical UX to the Streamlit chat.
 *
 * Extra vs Streamlit:
 *   - When a backup request triggers HITL, a banner appears with a direct link
 *     to the approval screen (also delivered as a push notification).
 */

import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import React, { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ChatBubble } from '../../components/ChatBubble';
import { ResultTable } from '../../components/ResultTable';
import { Colors, FontSize, Radius, Spacing } from '../../constants/theme';
import { apiClient } from '../../services/api';
import { useAppStore } from '../../stores/useAppStore';

interface Message {
  id:       string;
  role:     'user' | 'assistant';
  content:  string;
  columns?: string[];
  rows?:    unknown[][];
}

let _msgId = 0;
const nextId = () => String(++_msgId);

export default function ChatScreen() {
  const router                      = useRouter();
  const { threadId, setThreadId, setPendingHitl, pendingHitl, user } = useAppStore();
  const [messages, setMessages]     = useState<Message[]>([]);
  const [input,    setInput]        = useState('');
  const [loading,  setLoading]      = useState(false);
  const listRef                     = useRef<FlatList>(null);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput('');
    const userMsg: Message = { id: nextId(), role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const resp = await apiClient.query(text, threadId ?? undefined);
      setThreadId(resp.thread_id);

      if (resp.hitl_pending && resp.hitl_payload) {
        // HITL interrupt — push was already sent by backend, also set in store
        setPendingHitl({ threadId: resp.thread_id, payload: resp.hitl_payload as any });
        const assistantMsg: Message = {
          id:      nextId(),
          role:    'assistant',
          content: 'Backup request processed. A push notification has been sent to the DBA team for approval.',
        };
        setMessages((prev) => [...prev, assistantMsg]);
      } else {
        const assistantMsg: Message = {
          id:      nextId(),
          role:    'assistant',
          content: resp.final_result ?? 'Done.',
          columns: resp.columns,
          rows:    resp.rows,
        };
        setMessages((prev) => [...prev, assistantMsg]);
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'assistant', content: `Error: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [input, loading, threadId, setThreadId, setPendingHitl]);

  const clearChat = () => {
    setMessages([]);
    setThreadId(null);
    setPendingHitl(null);
  };

  const renderItem = ({ item }: { item: Message }) => (
    <View>
      <ChatBubble role={item.role} content={item.content} />
      {item.role === 'assistant' && item.columns && item.rows && item.rows.length > 0 && (
        <View style={styles.tableWrapper}>
          <ResultTable columns={item.columns} rows={item.rows} />
        </View>
      )}
    </View>
  );

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={90}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>OmniDBA</Text>
          <TouchableOpacity onPress={clearChat} style={styles.clearBtn}>
            <Ionicons name="trash-outline" size={18} color={Colors.textMuted} />
          </TouchableOpacity>
        </View>

        {/* HITL pending banner */}
        {pendingHitl && (
          <TouchableOpacity
            style={styles.hitlBanner}
            onPress={() =>
              router.push({
                pathname: '/hitl-approval',
                params:   { thread_id: pendingHitl.threadId },
              })
            }
          >
            <Ionicons name="warning-outline" size={16} color={Colors.black} />
            <Text style={styles.hitlBannerText}>
              Backup approval pending — tap to review
            </Text>
            <Ionicons name="chevron-forward" size={14} color={Colors.black} />
          </TouchableOpacity>
        )}

        {/* Message list */}
        {messages.length === 0 ? (
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>Ask your Oracle DB anything</Text>
            <Text style={styles.emptyHint}>
              "Show tablespace usage"{'\n'}
              "Top 10 SQL by elapsed time"{'\n'}
              "Run a full compressed backup tonight"
            </Text>
          </View>
        ) : (
          <FlatList
            ref={listRef}
            data={messages}
            keyExtractor={(m) => m.id}
            renderItem={renderItem}
            contentContainerStyle={styles.list}
            onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
          />
        )}

        {/* Thinking indicator */}
        {loading && (
          <View style={styles.thinking}>
            <ActivityIndicator color={Colors.primary} size="small" />
            <Text style={styles.thinkingText}>Agent is thinking…</Text>
          </View>
        )}

        {/* Input bar */}
        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="Ask about your Oracle database…"
            placeholderTextColor={Colors.textDisabled}
            multiline
            maxLength={500}
            returnKeyType="send"
            onSubmitEditing={sendMessage}
            editable={!loading}
          />
          <TouchableOpacity
            style={[styles.sendBtn, (!input.trim() || loading) && styles.sendBtnDisabled]}
            onPress={sendMessage}
            disabled={!input.trim() || loading}
          >
            <Ionicons name="send" size={20} color={Colors.white} />
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: Colors.background },
  flex:   { flex: 1 },
  header: {
    flexDirection:  'row',
    alignItems:     'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.md,
    paddingVertical:   Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
    backgroundColor:   Colors.surface,
  },
  headerTitle: { color: Colors.text, fontSize: FontSize.lg, fontWeight: '700' },
  clearBtn:    { padding: Spacing.xs },
  hitlBanner: {
    flexDirection:  'row',
    alignItems:     'center',
    backgroundColor: Colors.warning,
    paddingHorizontal: Spacing.md,
    paddingVertical:   Spacing.sm,
    gap: Spacing.sm,
  },
  hitlBannerText: {
    flex:       1,
    color:      Colors.black,
    fontWeight: '600',
    fontSize:   FontSize.sm,
  },
  list:   { paddingVertical: Spacing.sm },
  tableWrapper: { paddingHorizontal: Spacing.md, marginBottom: Spacing.sm },
  empty: {
    flex:           1,
    alignItems:     'center',
    justifyContent: 'center',
    padding:        Spacing.xl,
  },
  emptyTitle: {
    color:        Colors.textMuted,
    fontSize:     FontSize.lg,
    fontWeight:   '600',
    marginBottom: Spacing.md,
    textAlign:    'center',
  },
  emptyHint: {
    color:      Colors.textDisabled,
    fontSize:   FontSize.sm,
    lineHeight: FontSize.sm * 1.8,
    textAlign:  'center',
  },
  thinking: {
    flexDirection:  'row',
    alignItems:     'center',
    paddingHorizontal: Spacing.md,
    paddingVertical:   Spacing.sm,
    gap: Spacing.sm,
  },
  thinkingText: { color: Colors.textMuted, fontSize: FontSize.sm },
  inputRow: {
    flexDirection:  'row',
    alignItems:     'flex-end',
    paddingHorizontal: Spacing.md,
    paddingVertical:   Spacing.sm,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
    backgroundColor: Colors.surface,
    gap: Spacing.sm,
  },
  input: {
    flex:             1,
    backgroundColor:  Colors.surfaceRaised,
    borderWidth:      1,
    borderColor:      Colors.border,
    borderRadius:     Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingVertical:   Spacing.sm,
    color:            Colors.text,
    fontSize:         FontSize.md,
    maxHeight:        100,
  },
  sendBtn: {
    width:           44,
    height:          44,
    borderRadius:    22,
    backgroundColor: Colors.primary,
    alignItems:      'center',
    justifyContent:  'center',
  },
  sendBtnDisabled: { opacity: 0.4 },
});
