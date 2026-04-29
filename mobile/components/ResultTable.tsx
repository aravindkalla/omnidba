import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { Colors, FontSize, Spacing } from '../constants/theme';

interface Props {
  columns: string[];
  rows:    unknown[][];
  maxRows?: number;
}

export function ResultTable({ columns, rows, maxRows = 50 }: Props) {
  if (!columns.length || !rows.length) return null;

  const displayRows = rows.slice(0, maxRows);

  return (
    <View style={styles.container}>
      <ScrollView horizontal showsHorizontalScrollIndicator>
        <View>
          {/* Header */}
          <View style={[styles.row, styles.headerRow]}>
            {columns.map((col) => (
              <Text key={col} style={[styles.cell, styles.headerCell]}>
                {col}
              </Text>
            ))}
          </View>
          {/* Body */}
          {displayRows.map((row, ri) => (
            <View
              key={ri}
              style={[styles.row, ri % 2 === 1 && styles.rowAlt]}
            >
              {(row as unknown[]).map((val, ci) => (
                <Text key={ci} style={styles.cell} numberOfLines={1}>
                  {val == null ? '—' : String(val)}
                </Text>
              ))}
            </View>
          ))}
        </View>
      </ScrollView>
      {rows.length > maxRows && (
        <Text style={styles.truncated}>
          Showing {maxRows} of {rows.length} rows
        </Text>
      )}
    </View>
  );
}

const COL_WIDTH = 140;

const styles = StyleSheet.create({
  container: {
    borderWidth:   1,
    borderColor:   Colors.border,
    borderRadius:  6,
    overflow:      'hidden',
    marginTop:     Spacing.sm,
  },
  row: {
    flexDirection: 'row',
  },
  rowAlt: {
    backgroundColor: Colors.surfaceRaised,
  },
  headerRow: {
    backgroundColor: Colors.primary,
  },
  cell: {
    width:             COL_WIDTH,
    paddingVertical:   Spacing.xs,
    paddingHorizontal: Spacing.sm,
    fontSize:          FontSize.xs,
    color:             Colors.text,
    borderRightWidth:  1,
    borderRightColor:  Colors.border,
  },
  headerCell: {
    color:      Colors.white,
    fontWeight: '700',
    fontSize:   FontSize.sm,
  },
  truncated: {
    padding:   Spacing.sm,
    color:     Colors.textMuted,
    fontSize:  FontSize.xs,
    textAlign: 'center',
  },
});
