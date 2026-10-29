import React from 'react';
import { Text as RNText, TextProps as RNTextProps, StyleSheet } from 'react-native';
import { colors, typography } from './theme';

interface TextProps extends RNTextProps {
  color?: string;
  align?: 'auto' | 'left' | 'right' | 'center' | 'justify';
  numberOfLines?: number;
}

export const Heading: React.FC<TextProps & { level?: 1 | 2 | 3 }> = ({
  level = 1,
  style,
  children,
  color = colors.text,
  align = 'left',
  ...rest
}) => {
  const fontStyle = level === 1 ? typography.h1 : level === 2 ? typography.h2 : typography.h3;
  return (
    <RNText style={[fontStyle, { color, textAlign: align }, style]} {...rest}>
      {children}
    </RNText>
  );
};

export const BodyText: React.FC<TextProps & { bold?: boolean }> = ({
  bold = false,
  style,
  children,
  color = colors.text,
  align = 'left',
  ...rest
}) => {
  const fontStyle = bold ? typography.bodyBold : typography.body;
  return (
    <RNText style={[fontStyle, { color, textAlign: align }, style]} {...rest}>
      {children}
    </RNText>
  );
};

export const Caption: React.FC<TextProps> = ({
  style,
  children,
  color = colors.textSecondary,
  align = 'left',
  ...rest
}) => {
  return (
    <RNText style={[typography.caption, { color, textAlign: align }, style]} {...rest}>
      {children}
    </RNText>
  );
};

export const CodeText: React.FC<TextProps> = ({
  style,
  children,
  color = colors.cyan,
  align = 'left',
  ...rest
}) => {
  return (
    <RNText style={[typography.code, { color, textAlign: align }, style]} {...rest}>
      {children}
    </RNText>
  );
};
