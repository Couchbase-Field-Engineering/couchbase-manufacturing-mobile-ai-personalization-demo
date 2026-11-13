import { Ionicons } from '@expo/vector-icons';
import { Redirect, Tabs } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useAuth } from '@/src/session/AuthContext';
import { theme } from '@/src/theme';
import type { IconName } from '@/src/ui/components';

const ICONS: Record<string, [IconName, IconName]> = {
  index: ['today', 'today-outline'],
  products: ['cube', 'cube-outline'],
  assistant: ['sparkles', 'sparkles-outline'],
  orders: ['construct', 'construct-outline'],
  profile: ['person-circle', 'person-circle-outline'],
};

export default function TabsLayout() {
  const { ready, session } = useAuth();
  const insets = useSafeAreaInsets();
  if (ready && !session) return <Redirect href="/login" />;
  return (
    <Tabs
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarActiveTintColor: theme.color.red,
        tabBarInactiveTintColor: theme.color.faint,
        tabBarStyle: {
          backgroundColor: theme.color.surface,
          borderTopColor: theme.color.border,
          // Room for icon + label above the home indicator.
          height: 60 + insets.bottom,
          paddingBottom: insets.bottom,
        },
        tabBarItemStyle: { paddingVertical: 4 },
        // Phone layout in every viewport (the web build otherwise puts labels beside icons on wide windows).
        tabBarLabelPosition: 'below-icon',
        tabBarLabelStyle: { fontSize: theme.type.xs, fontWeight: '700', lineHeight: 17 },
        tabBarIcon: ({ focused, color, size }) => (
          <Ionicons name={(ICONS[route.name] ?? ICONS.index)[focused ? 0 : 1]} size={size - 2} color={focused ? theme.color.red : color} />
        ),
      })}
    >
      <Tabs.Screen name="index" options={{ title: 'Today' }} />
      <Tabs.Screen name="products" options={{ title: 'Products' }} />
      <Tabs.Screen name="assistant" options={{ title: 'Assistant' }} />
      <Tabs.Screen name="orders" options={{ title: 'Orders' }} />
      <Tabs.Screen name="profile" options={{ title: 'Profile' }} />
    </Tabs>
  );
}
