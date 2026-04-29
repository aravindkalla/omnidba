// OmniDBA — Design tokens
// Dark theme matching the existing Streamlit app's colour language

export const Colors = {
  // Brand
  primary:       '#1565C0',   // Oracle deep blue
  primaryLight:  '#1E88E5',
  accent:        '#FF6F00',   // Oracle orange (alerts, CTA)

  // Background / surface
  background:    '#0D1117',   // page background
  surface:       '#161B22',   // card / panel background
  surfaceRaised: '#1C2128',   // elevated card (modal, sheet)
  border:        '#30363D',

  // Text
  text:          '#E6EDF3',
  textMuted:     '#8B949E',
  textDisabled:  '#484F58',

  // Status
  ok:            '#3FB950',   // green
  warning:       '#D29922',   // amber
  critical:      '#F85149',   // red
  unknown:       '#8B949E',   // grey

  // Misc
  white:         '#FFFFFF',
  black:         '#000000',
  overlay:       'rgba(0,0,0,0.6)',
};

export const StatusColor: Record<string, string> = {
  ok:       Colors.ok,
  warning:  Colors.warning,
  critical: Colors.critical,
  unknown:  Colors.unknown,
};

export const Spacing = {
  xs:   4,
  sm:   8,
  md:   16,
  lg:   24,
  xl:   32,
  xxl:  48,
};

export const Radius = {
  sm:   6,
  md:   10,
  lg:   16,
  full: 999,
};

export const FontSize = {
  xs:   11,
  sm:   13,
  md:   15,
  lg:   17,
  xl:   20,
  xxl:  26,
};

export const FontWeight = {
  regular: '400' as const,
  medium:  '500' as const,
  bold:    '700' as const,
};
