import React, { createContext, useContext, useEffect, useState } from 'react';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import * as LocalAuthentication from 'expo-local-authentication';
import { apiClient } from '../api/client';
import { User } from '../types/models';

interface AuthContextValue {
  user: User | null;
  token: string | null;
  refreshToken: string | null;
  loading: boolean;
  isBiometricsSupported: boolean;
  isBiometricsEnrolled: boolean;
  isBiometricLockActive: boolean;
  biometricsEnabled: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  loginWithGoogle: (idToken: string) => Promise<void>;
  unlockWithBiometrics: () => Promise<boolean>;
  setBiometricsEnabled: (enabled: boolean) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

// Claves compatibles con el formato estricto de expo-secure-store ([a-zA-Z0-9._-])
const SECURE_TOKEN_KEY = 'smart_ocr_jwt_token';
const SECURE_REFRESH_KEY = 'smart_ocr_refresh_token';
const SECURE_USER_KEY = 'smart_ocr_user_data';
const SECURE_BIOMETRICS_KEY = 'smart_ocr_biometrics_pref';

// Claves legadas de AsyncStorage para migración y limpieza automática
const LEGACY_TOKEN_KEY = '@smart_ocr_jwt_token';
const LEGACY_USER_KEY = '@smart_ocr_user_data';

async function secureSetItem(key: string, value: string): Promise<void> {
  if (Platform.OS === 'web') {
    await AsyncStorage.setItem(key, value);
    return;
  }
  await SecureStore.setItemAsync(key, value, {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
}

async function secureGetItem(key: string): Promise<string | null> {
  if (Platform.OS === 'web') {
    return AsyncStorage.getItem(key);
  }
  return SecureStore.getItemAsync(key);
}

async function secureDeleteItem(key: string): Promise<void> {
  if (Platform.OS === 'web') {
    await AsyncStorage.removeItem(key);
    return;
  }
  await SecureStore.deleteItemAsync(key);
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  // Estados de biometría (Fase 2)
  const [isBiometricsSupported, setIsBiometricsSupported] = useState<boolean>(false);
  const [isBiometricsEnrolled, setIsBiometricsEnrolled] = useState<boolean>(false);
  const [biometricsEnabled, setBiometricsEnabledState] = useState<boolean>(true);
  const [isBiometricLockActive, setIsBiometricLockActive] = useState<boolean>(false);

  useEffect(() => {
    (async () => {
      try {
        // 1. Detectar hardware y enrolamiento biométrico en el dispositivo
        if (Platform.OS !== 'web') {
          try {
            const hasHardware = await LocalAuthentication.hasHardwareAsync();
            const isEnrolled = await LocalAuthentication.isEnrolledAsync();
            setIsBiometricsSupported(hasHardware);
            setIsBiometricsEnrolled(isEnrolled);
          } catch {
            setIsBiometricsSupported(false);
            setIsBiometricsEnrolled(false);
          }
        }

        // 2. Recuperar sesión cifrada desde SecureStore
        let [savedToken, savedRefresh, savedUser, savedBioPref] = await Promise.all([
          secureGetItem(SECURE_TOKEN_KEY),
          secureGetItem(SECURE_REFRESH_KEY),
          secureGetItem(SECURE_USER_KEY),
          secureGetItem(SECURE_BIOMETRICS_KEY),
        ]);

        // Migración transparente desde AsyncStorage en texto plano hacia SecureStore cifrado
        if (!savedToken || !savedUser) {
          const [legacyToken, legacyUser] = await Promise.all([
            AsyncStorage.getItem(LEGACY_TOKEN_KEY),
            AsyncStorage.getItem(LEGACY_USER_KEY),
          ]);
          if (legacyToken && legacyUser) {
            savedToken = legacyToken;
            savedUser = legacyUser;
            await Promise.all([
              secureSetItem(SECURE_TOKEN_KEY, legacyToken),
              secureSetItem(SECURE_USER_KEY, legacyUser),
              AsyncStorage.multiRemove([LEGACY_TOKEN_KEY, LEGACY_USER_KEY]),
            ]);
          }
        }

        const bioActive = savedBioPref !== 'false'; // Habilitado por defecto si el hardware lo soporta
        setBiometricsEnabledState(bioActive);

        if (savedToken && savedUser) {
          setToken(savedToken);
          setRefreshToken(savedRefresh);
          setUser(JSON.parse(savedUser));

          // Si hay sesión y la biometría está disponible, activar candado biométrico al inicio
          if (Platform.OS !== 'web' && bioActive) {
            const hasHardware = await LocalAuthentication.hasHardwareAsync().catch(() => false);
            const isEnrolled = await LocalAuthentication.isEnrolledAsync().catch(() => false);
            if (hasHardware && isEnrolled) {
              setIsBiometricLockActive(true);
            }
          }
        }
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const persistSession = async (
    accessToken: string,
    newRefreshToken: string | null | undefined,
    userData: User
  ) => {
    setToken(accessToken);
    setRefreshToken(newRefreshToken || null);
    setUser(userData);
    setIsBiometricLockActive(false);
    await Promise.all([
      secureSetItem(SECURE_TOKEN_KEY, accessToken),
      secureSetItem(SECURE_USER_KEY, JSON.stringify(userData)),
      newRefreshToken
        ? secureSetItem(SECURE_REFRESH_KEY, newRefreshToken)
        : Promise.resolve(),
    ]);
  };

  const login = async (email: string, password: string) => {
    const res = await apiClient.login(email, password);
    await persistSession(res.access_token, res.refresh_token, res.user);
  };

  const register = async (
    email: string,
    password: string,
    fullName: string
  ) => {
    const res = await apiClient.register(email, password, fullName);
    await persistSession(res.access_token, res.refresh_token, res.user);
  };

  const loginWithGoogle = async (idToken: string) => {
    const res = await apiClient.loginWithGoogle(idToken);
    await persistSession(res.access_token, res.refresh_token, res.user);
  };

  const unlockWithBiometrics = async (): Promise<boolean> => {
    if (Platform.OS === 'web') {
      setIsBiometricLockActive(false);
      return true;
    }
    try {
      const result = await LocalAuthentication.authenticateAsync({
        promptMessage: 'Desbloquear Smart OCR Scanner',
        fallbackLabel: 'Ingresar con contraseña',
        cancelLabel: 'Cancelar',
        disableDeviceFallback: false,
      });
      if (result.success) {
        setIsBiometricLockActive(false);
        return true;
      }
      return false;
    } catch {
      return false;
    }
  };

  const setBiometricsEnabled = async (enabled: boolean) => {
    setBiometricsEnabledState(enabled);
    await secureSetItem(SECURE_BIOMETRICS_KEY, enabled ? 'true' : 'false');
  };

  const logout = async () => {
    setToken(null);
    setRefreshToken(null);
    setUser(null);
    setIsBiometricLockActive(false);
    await Promise.all([
      secureDeleteItem(SECURE_TOKEN_KEY),
      secureDeleteItem(SECURE_REFRESH_KEY),
      secureDeleteItem(SECURE_USER_KEY),
      AsyncStorage.multiRemove([LEGACY_TOKEN_KEY, LEGACY_USER_KEY]),
    ]);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        refreshToken,
        loading,
        isBiometricsSupported,
        isBiometricsEnrolled,
        isBiometricLockActive,
        biometricsEnabled,
        login,
        register,
        loginWithGoogle,
        unlockWithBiometrics,
        setBiometricsEnabled,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextValue => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth debe usarse dentro de AuthProvider');
  return ctx;
};
