import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  BackHandler,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { AuthProvider, useAuth } from './src/context/AuthContext';
import { AuthScreen } from './src/screens/AuthScreen';
import { SmartCameraScreen } from './src/screens/SmartCameraScreen';
import { HistoryScreen } from './src/screens/HistoryScreen';
import { ScanDetailScreen } from './src/screens/ScanDetailScreen';
import { ScanItem } from './src/types/models';

const MainNavigator: React.FC = () => {
  const {
    user,
    loading,
    isBiometricLockActive,
    unlockWithBiometrics,
    logout,
  } = useAuth();

  // Inicia siempre en la pantalla de Historial al ingresar a la app
  const [activeScreen, setActiveScreen] = useState<
    'HISTORY' | 'CAMERA' | 'DETAIL'
  >('HISTORY');
  const [selectedScan, setSelectedScan] = useState<ScanItem | null>(null);

  // Navegación nativa fluida con Android BackHandler (Fase 2)
  useEffect(() => {
    const onBackPress = () => {
      if (activeScreen === 'DETAIL' || activeScreen === 'CAMERA') {
        setActiveScreen('HISTORY');
        return true; // Intercepta el botón Atrás evitando cerrar la app
      }
      return false; // Permite el comportamiento por defecto en HistoryScreen
    };

    const sub = BackHandler.addEventListener('hardwareBackPress', onBackPress);
    return () => sub.remove();
  }, [activeScreen]);

  // Si el candado biométrico está activo, intentar desbloquear automáticamente al abrir
  useEffect(() => {
    if (isBiometricLockActive) {
      unlockWithBiometrics();
    }
  }, [isBiometricLockActive, unlockWithBiometrics]);

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

  // Pantalla de bloqueo biométrico (Fase 2)
  if (isBiometricLockActive) {
    return (
      <View style={styles.lockContainer}>
        <View style={styles.lockCard}>
          <Text style={styles.lockBadge}>SMART OCR • SEGURIDAD BIOMÉTRICA</Text>
          <Text style={styles.lockTitle}>Sesión Protegida</Text>
          <Text style={styles.lockSubtitle}>
            Hola, {user.full_name} ({user.email})
          </Text>
          <Text style={styles.lockHelp}>
            Confirma tu identidad mediante huella dactilar, FaceID o PIN del dispositivo para acceder a tus documentos contables.
          </Text>

          <TouchableOpacity
            style={styles.biometricBtn}
            onPress={unlockWithBiometrics}
          >
            <Text style={styles.biometricBtnText}>
              👆 Desbloquear con Huella / FaceID
            </Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.logoutBtn} onPress={logout}>
            <Text style={styles.logoutBtnText}>
              Cerrar Sesión / Cambiar de cuenta
            </Text>
          </TouchableOpacity>
        </View>
      </View>
    );
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
  lockContainer: {
    flex: 1,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    padding: 24,
  },
  lockCard: {
    backgroundColor: '#1E293B',
    borderRadius: 20,
    padding: 28,
    borderWidth: 1,
    borderColor: '#334155',
    alignItems: 'center',
  },
  lockBadge: {
    color: '#38BDF8',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.5,
    marginBottom: 12,
  },
  lockTitle: {
    color: '#F8FAFC',
    fontSize: 22,
    fontWeight: '700',
    marginBottom: 6,
  },
  lockSubtitle: {
    color: '#94A3B8',
    fontSize: 14,
    marginBottom: 16,
    textAlign: 'center',
  },
  lockHelp: {
    color: '#64748B',
    fontSize: 13,
    lineHeight: 19,
    textAlign: 'center',
    marginBottom: 24,
  },
  biometricBtn: {
    backgroundColor: '#0284C7',
    paddingVertical: 14,
    paddingHorizontal: 24,
    borderRadius: 12,
    width: '100%',
    alignItems: 'center',
    marginBottom: 14,
  },
  biometricBtnText: {
    color: '#FFFFFF',
    fontWeight: '700',
    fontSize: 15,
  },
  logoutBtn: {
    paddingVertical: 10,
    paddingHorizontal: 16,
  },
  logoutBtnText: {
    color: '#94A3B8',
    fontSize: 13,
    fontWeight: '500',
  },
});
