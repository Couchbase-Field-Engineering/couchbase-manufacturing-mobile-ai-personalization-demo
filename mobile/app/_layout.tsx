import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { DataProvider } from '@/src/data/DataContext';
import { AuthProvider } from '@/src/session/AuthContext';
import { theme } from '@/src/theme';
import { DeviceFrame } from '@/src/ui/DeviceFrame';

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <DataProvider>
          <DeviceFrame>
            <StatusBar style="light" />
            <Stack
              screenOptions={{
                headerStyle: { backgroundColor: theme.color.surface },
                headerTintColor: theme.color.ink,
                headerTitleStyle: { fontWeight: '800' },
                headerShadowVisible: false,
                contentStyle: { backgroundColor: theme.color.bg },
              }}
            >
              <Stack.Screen name="index" options={{ headerShown: false }} />
              <Stack.Screen name="login" options={{ headerShown: false }} />
              <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
              <Stack.Screen name="product" options={{ title: 'Product', presentation: 'modal' }} />
              <Stack.Screen name="order" options={{ title: 'Production order', presentation: 'modal' }} />
              <Stack.Screen name="check" options={{ title: 'Line check', presentation: 'modal' }} />
              <Stack.Screen name="confirm" options={{ title: 'Review & submit', presentation: 'modal' }} />
            </Stack>
          </DeviceFrame>
        </DataProvider>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
