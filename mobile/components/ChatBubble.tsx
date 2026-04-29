import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Colors, FontSize, Radius, Spacing } from '../constants/theme';

interface Props {
  role:    'user' | 'assistant';
  content: string;
}

export function ChatBubble({ role, content }: Props) {
  const isUser = role === 'user';
  return (
    <View style={[styles.row, isUser ? styles.rowUser : styles.rowAssistant]}>
      {!isUser && (
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>DB</Text>
        </View>
      )}
      <View
        style={[
          styles.bubble,
          isUser ? styles.bubbleUser : styles.bubbleAssistant,
        ]}
      >
        <Text style={[styles.text, isUser ? styles.textUser : styles.textAssistant]}>
          {content}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection:  'row',
    alignItems:     'flex-end',
    marginVertical: Spacing.xs,
    paddingHorizontal: Spacing.md,
  },
  rowUser: {
    justifyContent: 'flex-end',
  },
  rowAssistant: {
    justifyContent: 'flex-start',
  },
  avatar: {
    width:           32,
    height:          32,
    borderRadius:    Radius.full,
    backgroundColor: Colors.primary,
    alignItems:      'center',
    justifyContent:  'center',
    marginRight:     Spacing.sm,
    marginBottom:    2,
  },
  avatarText: {
    color:     Colors.white,
    fontSize:  FontSize.xs,
    fontWeight: '700',
  },
  bubble: {
    maxWidth:     '75%',
    paddingVertical:   Spacing.sm,
    paddingHorizontal: Spacing.md,
    borderRadius:  Radius.lg,
  },
  bubbleUser: {
    backgroundColor: Colors.primary,
    borderBottomRightRadius: Radius.sm,
  },
  bubbleAssistant: {
    backgroundColor: Colors.surface,
    borderBottomLeftRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  text: {
    fontSize:   FontSize.md,
    lineHeight: FontSize.md * 1.5,
  },
  textUser: {
    color: Colors.white,
  },
  textAssistant: {
    color: Colors.text,
  },
});
