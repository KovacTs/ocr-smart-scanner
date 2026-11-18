import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import * as LocalAuthentication from 'expo-local-authentication';
import {
  apiClient,
  clearAccessTokenOverride,
  registerSessionHandler,
} from '../api/client';
import { AuthResponse, User } from '../types/models';

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

function isJwtExpired(jwtToken: string): boolean {
  try {
    const parts = jwtToken.split('.');
    if (parts.length !== 3) return true;
    const base64Url = parts[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const chars =
      'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
    let str = '';
    for (let i = 0; i < padded.length; i += 4) {
      const e1 = chars.indexOf(padded.charAt(i));
      const e2 = chars.indexOf(padded.charAt(i + 1));
      const e3 = chars.indexOf(padded.charAt(i + 2));
      const e4 = chars.indexOf(padded.charAt(i + 3));
      const c1 = (e1 << 2) | (e2 >> 4);
      const c2 = ((e2 & 15) << 4) | (e3 >> 2);
      const c3 = ((e3 & 3) << 6) | e4;
      str += String.fromCharCode(c1);
      if (e3 !== 64) str += String.fromCharCode(c2);
      if (e4 !== 64) str += String.fromCharCode(c3);
    }
    const payload = JSON.parse(str);
    if (!payload.exp) return false;
    const nowSec = Math.floor(Date.now() / 1000);
    return payload.exp <= nowSec + 60;
  } catch {
    return false;
  }
}

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

  const refreshTokenRef = useRef<string | null>(null);
  useEffect(() => {
    refreshTokenRef.current = refreshToken;
  }, [refreshToken]);

  const logout = useCallback(async () => {
    clearAccessTokenOverride();
    setToken(null);
    setRefreshToken(null);
    refreshTokenRef.current = null;
    setUser(null);
    setIsBiometricLockActive(false);
    await Promise.all([
      secureDeleteItem(SECURE_TOKEN_KEY),
      secureDeleteItem(SECURE_REFRESH_KEY),
      secureDeleteItem(SECURE_USER_KEY),
      AsyncStorage.multiRemove([LEGACY_TOKEN_KEY, LEGACY_USER_KEY]),
    ]);
  }, []);

  const persistSession = useCallback(
    async (
      accessToken: string,
      newRefreshToken: string | null | undefined,
      userData: User,
      unlockBiometrics = true
    ) => {
      clearAccessTokenOverride();
      setToken(accessToken);
      setRefreshToken(newRefreshToken || null);
      refreshTokenRef.current = newRefreshToken || null;
      setUser(userData);
      if (unlockBiometrics) {
        setIsBiometricLockActive(false);
      }
      await Promise.all([
        secureSetItem(SECURE_TOKEN_KEY, accessToken),
        secureSetItem(SECURE_USER_KEY, JSON.stringify(userData)),
        newRefreshToken
          ? secureSetItem(SECURE_REFRESH_KEY, newRefreshToken)
          : Promise.resolve(),
      ]);
    },
    []
  );

  useEffect(() => {
    registerSessionHandler({
      getRefreshToken: async () => {
        if (refreshTokenRef.current) return refreshTokenRef.current;
        return secureGetItem(SECURE_REFRESH_KEY);
      },
      onSessionRefreshed: async (auth: AuthResponse) => {
        await persistSession(
          auth.access_token,
          auth.refresh_token,
          auth.user,
          false
        );
      },
      onSessionExpired: async () => {
        await logout();
      },
    });
  }, [persistSession, logout]);

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
          // Si el access_token guardado ya expiró, intentar renovarlo silenciosamente con el refresh_token
          if (isJwtExpired(savedToken)) {
            if (savedRefresh) {
              try {
                const refreshed = await apiClient.refreshAccessToken(savedRefresh);
                savedToken = refreshed.access_token;
                savedRefresh = refreshed.refresh_token || savedRefresh;
                savedUser = JSON.stringify(refreshed.user);
                await Promise.all([
                  secureSetItem(SECURE_TOKEN_KEY, savedToken),
                  secureSetItem(SECURE_USER_KEY, savedUser),
                  savedRefresh
                    ? secureSetItem(SECURE_REFRESH_KEY, savedRefresh)
                    : Promise.resolve(),
                ]);
              } catch {
                // Si el refresh_token también expiró o fue revocado, limpiar sesión para pedir login limpio
                await logout();
                return;
              }
            } else {
              // Sesión antigua sin refresh_token y con JWT vencido -> pedir login limpio
              await logout();
              return;
            }
          }

          setToken(savedToken);
          setRefreshToken(savedRefresh);
          refreshTokenRef.current = savedRefresh;
          setUser(JSON.parse(savedUser));

          // Si hay sesión válida y la biometría está disponible, activar candado biométrico al inicio
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
  }, [logout]);

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
        // Si el token expiró mientras la pantalla estaba bloqueada, renovarlo al desbloquear
        if (token && isJwtExpired(token)) {
          const activeRefresh =
            refreshTokenRef.current || (await secureGetItem(SECURE_REFRESH_KEY));
          if (activeRefresh) {
            try {
              const refreshed = await apiClient.refreshAccessToken(activeRefresh);
              await persistSession(
                refreshed.access_token,
                refreshed.refresh_token,
                refreshed.user,
                true
              );
              return true;
            } catch {
              await logout();
              return false;
            }
          } else {
            await logout();
            return false;
          }
        }
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
