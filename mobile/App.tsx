import React, { useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { AuthProvider, useAuth } from './src/context/AuthContext';
import { AuthScreen } from './src/screens/AuthScreen';
import { SmartCameraScreen } from './src/screens/SmartCameraScreen';
import { HistoryScreen } from './src/screens/HistoryScreen';
import { ScanDetailScreen } from './src/screens/ScanDetailScreen';
import { ScanItem } from './src/types/models';

const MainNavigator: React.FC = () => {
  const { user, loading } = useAuth();
  // Inicia siempre en la pantalla de Historial al ingresar a la app
  const [activeScreen, setActiveScreen] = useState<
    'HISTORY' | 'CAMERA' | 'DETAIL'
  >('HISTORY');
  const [selectedScan, setSelectedScan] = useState<ScanItem | null>(null);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#38BDF8" />
      </View>
    );
  }

  if (!user) {
    return <AuthScreen />;
  }

  if (activeScreen === 'DETAIL' && selectedScan) {
    return (
      <ScanDetailScreen
        scan={selectedScan}
        onBack={() => setActiveScreen('HISTORY')}
        onScanUpdated={(updated) => setSelectedScan(updated)}
      />
    );
  }

  if (activeScreen === 'CAMERA') {
    return (
      <SmartCameraScreen
        onNavigateHistory={() => setActiveScreen('HISTORY')}
        onScanCompleted={(newScan) => {
          setSelectedScan(newScan);
          setActiveScreen('DETAIL');
        }}
      />
    );
  }

  return (
    <HistoryScreen
      onOpenCamera={() => setActiveScreen('CAMERA')}
      onSelectScan={(scan) => {
        setSelectedScan(scan);
        setActiveScreen('DETAIL');
      }}
    />
  );
};

export default function App() {
  return (
    <AuthProvider>
      <StatusBar style="light" />
      <MainNavigator />
    </AuthProvider>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    alignItems: 'center',
  },
});
