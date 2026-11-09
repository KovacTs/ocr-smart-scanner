import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  apiClient,
  getApiBaseUrl,
  setCustomApiBaseUrl,
} from '../api/client';
import { useAuth } from '../context/AuthContext';

export const AuthScreen: React.FC = () => {
  const { login, register, loginWithGoogle, isBiometricsSupported } = useAuth();
  const [isRegister, setIsRegister] = useState(false);
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Modal interactivo de autenticación y vinculación con Google (Fase 2)
  const [showGoogleModal, setShowGoogleModal] = useState(false);
  const [googleEmailInput, setGoogleEmailInput] = useState('');
  const [googleNameInput, setGoogleNameInput] = useState('');

  const [checkingServer, setCheckingServer] = useState(true);
  const [serverStatus, setServerStatus] = useState<{
    ok: boolean;
    url: string;
    message: string;
  }>({
    ok: false,
    url: getApiBaseUrl(),
    message: 'Verificando conexión con el backend...',
  });
  const [showServerConfig, setShowServerConfig] = useState(false);
  const [customUrlInput, setCustomUrlInput] = useState(getApiBaseUrl());

  const verifyConnection = useCallback(async () => {
    setCheckingServer(true);
    const status = await apiClient.checkServerHealth();
    setServerStatus(status);
    setCustomUrlInput(status.url);
    setCheckingServer(false);
  }, []);

  useEffect(() => {
    verifyConnection();
  }, [verifyConnection]);

  const handleApplyServerUrl = async () => {
    const updated = setCustomApiBaseUrl(customUrlInput);
    setCustomUrlInput(updated);
    await verifyConnection();
  };

  const handleResetAutoIp = async () => {
    const updated = setCustomApiBaseUrl('AUTO');
    setCustomUrlInput(updated);
    await verifyConnection();
  };

  const handleFillDemoAccount = () => {
    setIsRegister(false);
    setEmail('auditor@empresa.cl');
    setPassword('PasswordSeguro123!');
  };

  const handleSubmit = async () => {
    if (!email.trim() || !password.trim()) {
      Alert.alert('Campos requeridos', 'Ingresa tu correo y contraseña.');
      return;
    }
    if (isRegister && !fullName.trim()) {
      Alert.alert('Nombre requerido', 'Ingresa tu nombre completo.');
      return;
    }

    setSubmitting(true);
    try {
      if (isRegister) {
        await register(email.trim(), password, fullName.trim());
      } else {
        await login(email.trim(), password);
      }
    } catch (err: any) {
      await verifyConnection();
      Alert.alert('Error de autenticación', err.message || 'Intenta nuevamente');
    } finally {
      setSubmitting(false);
    }
  };

  const openGoogleModal = () => {
    setGoogleEmailInput(email.trim() || 'tu_correo@gmail.com');
    setGoogleNameInput(fullName.trim() || 'Usuario Google');
    setShowGoogleModal(true);
  };

  const handleConfirmGoogleSignIn = async (
    emailOverride?: string,
    nameOverride?: string
  ) => {
    const targetEmail = (emailOverride || googleEmailInput).trim().toLowerCase();
    const targetName =
      (nameOverride || googleNameInput).trim() || 'Usuario Google';

    if (!targetEmail || !targetEmail.includes('@')) {
      Alert.alert(
        'Correo Requerido',
        'Ingresa una dirección de correo válida para vincular tu cuenta con Google.'
      );
      return;
    }

    setSubmitting(true);
    try {
      const mockSub = `google_sub_${Math.abs(
        targetEmail.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0)
      )}`;
      const idToken = `mock_google_:${targetEmail}:${targetName}:${mockSub}`;
      setShowGoogleModal(false);
      await loginWithGoogle(idToken);
    } catch (err: any) {
      await verifyConnection();
      Alert.alert(
        'Error con Google',
        err.message || 'No se pudo iniciar sesión con Google.'
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <Text style={styles.badge}>SMART OCR • WEBP • EXCEL</Text>
        <Text style={styles.title}>
          {isRegister ? 'Crear Cuenta' : 'Iniciar Sesión'}
        </Text>
        <Text style={styles.subtitle}>
          Escaneo documental con validación de enfoque en tiempo real y
          normalización inteligente de facturas y boletas.
        </Text>

        {/* Estado de conexión en vivo con auto-detección de IP WiFi */}
        <TouchableOpacity
          style={[
            styles.statusBanner,
            serverStatus.ok ? styles.statusBannerOk : styles.statusBannerError,
          ]}
          onPress={() => setShowServerConfig(!showServerConfig)}
          activeOpacity={0.8}
        >
          <View style={styles.statusHeaderRow}>
            <Text
              style={[
                styles.statusTitle,
                serverStatus.ok ? styles.statusTextOk : styles.statusTextError,
              ]}
            >
              {checkingServer
                ? '⏳ Verificando servidor...'
                : serverStatus.ok
                ? '🟢 Servidor Conectado (Auto-IP WiFi)'
                : '🔴 Sin conexión al Backend'}
            </Text>
            <Text style={styles.statusConfigToggle}>
              {showServerConfig ? 'Ocultar IP ▲' : 'Cambiar IP ▼'}
            </Text>
          </View>
          <Text style={styles.statusUrlText} numberOfLines={1}>
            API: {serverStatus.url}
          </Text>
        </TouchableOpacity>

        {showServerConfig && (
          <View style={styles.serverConfigBox}>
            <Text style={styles.serverConfigLabel}>
              URL o IP del Backend (usa AUTO para detectar tu WiFi actual):
            </Text>
            <TextInput
              style={styles.serverInput}
              placeholder="http://192.168.1.168:8000/api o AUTO"
              placeholderTextColor="#64748B"
              autoCapitalize="none"
              autoCorrect={false}
              value={customUrlInput}
              onChangeText={setCustomUrlInput}
            />
            <View style={styles.serverButtonsRow}>
              <TouchableOpacity
                style={styles.serverActionBtn}
                onPress={handleApplyServerUrl}
              >
                <Text style={styles.serverActionBtnText}>Probar y Guardar</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.serverAutoBtn}
                onPress={handleResetAutoIp}
              >
                <Text style={styles.serverAutoBtnText}>Detectar IP Auto</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}

        {isRegister && (
          <TextInput
            style={styles.input}
            placeholder="Nombre Completo"
            placeholderTextColor="#64748B"
            value={fullName}
            onChangeText={setFullName}
          />
        )}

        <TextInput
          style={styles.input}
          placeholder="Correo Electrónico"
          placeholderTextColor="#64748B"
          autoCapitalize="none"
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
        />

        <TextInput
          style={styles.input}
          placeholder="Contraseña (mín. 6 caracteres)"
          placeholderTextColor="#64748B"
          secureTextEntry
          value={password}
          onChangeText={setPassword}
        />

        <TouchableOpacity
          style={styles.primaryButton}
          onPress={handleSubmit}
          disabled={submitting}
        >
          {submitting ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <Text style={styles.primaryButtonText}>
              {isRegister ? 'Registrarse' : 'Entrar al Escáner'}
            </Text>
          )}
        </TouchableOpacity>

        {!isRegister && (
          <TouchableOpacity
            style={styles.demoButton}
            onPress={handleFillDemoAccount}
          >
            <Text style={styles.demoButtonText}>
              Autocompletar cuenta de prueba (auditor@empresa.cl)
            </Text>
          </TouchableOpacity>
        )}

        <View style={styles.dividerRow}>
          <View style={styles.dividerLine} />
          <Text style={styles.dividerText}>O CONTINUAR CON</Text>
          <View style={styles.dividerLine} />
        </View>

        <TouchableOpacity
          style={styles.googleButton}
          onPress={openGoogleModal}
          disabled={submitting}
        >
          <Text style={styles.googleButtonText}>🌐 Iniciar Sesión con Google</Text>
        </TouchableOpacity>

        {isBiometricsSupported && (
          <Text style={styles.biometricBadge}>
            🛡️ Desbloqueo Biométrico (Huella / FaceID) compatible
          </Text>
        )}

        <TouchableOpacity
          style={styles.switchButton}
          onPress={() => setIsRegister(!isRegister)}
        >
          <Text style={styles.switchText}>
            {isRegister
              ? '¿Ya tienes cuenta? Inicia sesión'
              : '¿No tienes cuenta? Regístrate aquí'}
          </Text>
        </TouchableOpacity>
      </View>

      {/* Modal Interactivo de Inicio y Vinculación con Google (Fase 2) */}
      <Modal
        visible={showGoogleModal}
        transparent
        animationType="fade"
        onRequestClose={() => setShowGoogleModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalBadge}>GOOGLE OAUTH2 • VINCULACIÓN</Text>
            <Text style={styles.modalTitle}>Vincular Cuenta de Google</Text>
            <Text style={styles.modalSubtitle}>
              Ingresa el correo que deseas asociar. Si este correo ya tiene facturas o historial registrado, el sistema vinculará tu Google ID automáticamente para que puedas ingresar con un solo toque.
            </Text>

            <Text style={styles.modalLabel}>Correo de la Cuenta Google</Text>
            <TextInput
              style={styles.modalInput}
              placeholder="ejemplo@gmail.com o usuario@empresa.cl"
              placeholderTextColor="#64748B"
              autoCapitalize="none"
              keyboardType="email-address"
              value={googleEmailInput}
              onChangeText={setGoogleEmailInput}
            />

            <Text style={styles.modalLabel}>Nombre del Titular (Opcional)</Text>
            <TextInput
              style={styles.modalInput}
              placeholder="Nombre del usuario"
              placeholderTextColor="#64748B"
              value={googleNameInput}
              onChangeText={setGoogleNameInput}
            />

            <TouchableOpacity
              style={styles.modalPrimaryBtn}
              onPress={() => handleConfirmGoogleSignIn()}
              disabled={submitting}
            >
              {submitting ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={styles.modalPrimaryBtnText}>
                  Vincular e Iniciar con Google
                </Text>
              )}
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.modalQuickBtn}
              onPress={() =>
                handleConfirmGoogleSignIn('auditor@empresa.cl', 'Auditor Empresa')
              }
            >
              <Text style={styles.modalQuickBtnText}>
                ⚡ Probar vinculación a cuenta auditor@empresa.cl
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.modalCancelBtn}
              onPress={() => setShowGoogleModal(false)}
            >
              <Text style={styles.modalCancelBtnText}>Cancelar</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    backgroundColor: '#1E293B',
    borderRadius: 16,
    padding: 24,
    borderWidth: 1,
    borderColor: '#334155',
  },
  badge: {
    color: '#38BDF8',
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.2,
    marginBottom: 8,
  },
  title: {
    color: '#F8FAFC',
    fontSize: 26,
    fontWeight: '800',
    marginBottom: 8,
  },
  subtitle: {
    color: '#94A3B8',
    fontSize: 14,
    lineHeight: 20,
    marginBottom: 16,
  },
  statusBanner: {
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 1,
    marginBottom: 14,
  },
  statusBannerOk: {
    backgroundColor: 'rgba(16, 185, 129, 0.12)',
    borderColor: 'rgba(16, 185, 129, 0.4)',
  },
  statusBannerError: {
    backgroundColor: 'rgba(239, 68, 68, 0.12)',
    borderColor: 'rgba(239, 68, 68, 0.45)',
  },
  statusHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 2,
  },
  statusTitle: {
    fontSize: 12,
    fontWeight: '700',
  },
  statusTextOk: {
    color: '#34D399',
  },
  statusTextError: {
    color: '#F87171',
  },
  statusConfigToggle: {
    color: '#94A3B8',
    fontSize: 11,
    fontWeight: '600',
  },
  statusUrlText: {
    color: '#CBD5E1',
    fontSize: 11,
    fontFamily: 'monospace',
  },
  serverConfigBox: {
    backgroundColor: '#0F172A',
    borderRadius: 10,
    padding: 12,
    borderWidth: 1,
    borderColor: '#334155',
    marginBottom: 14,
  },
  serverConfigLabel: {
    color: '#94A3B8',
    fontSize: 12,
    marginBottom: 8,
  },
  serverInput: {
    backgroundColor: '#1E293B',
    color: '#F8FAFC',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 13,
    borderWidth: 1,
    borderColor: '#475569',
    marginBottom: 10,
  },
  serverButtonsRow: {
    flexDirection: 'row',
    gap: 8,
  },
  serverActionBtn: {
    flex: 1,
    backgroundColor: '#0284C7',
    paddingVertical: 9,
    borderRadius: 8,
    alignItems: 'center',
  },
  serverActionBtnText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '700',
  },
  serverAutoBtn: {
    flex: 1,
    backgroundColor: '#334155',
    paddingVertical: 9,
    borderRadius: 8,
    alignItems: 'center',
  },
  serverAutoBtnText: {
    color: '#E2E8F0',
    fontSize: 12,
    fontWeight: '600',
  },
  input: {
    backgroundColor: '#0F172A',
    color: '#F8FAFC',
    borderRadius: 10,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 15,
    borderWidth: 1,
    borderColor: '#334155',
    marginBottom: 14,
  },
  primaryButton: {
    backgroundColor: '#0284C7',
    borderRadius: 10,
    paddingVertical: 15,
    alignItems: 'center',
    marginTop: 6,
  },
  primaryButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '700',
  },
  demoButton: {
    marginTop: 12,
    paddingVertical: 8,
    alignItems: 'center',
    backgroundColor: 'rgba(56, 189, 248, 0.08)',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.25)',
  },
  demoButtonText: {
    color: '#7DD3FC',
    fontSize: 12,
    fontWeight: '600',
  },
  switchButton: {
    marginTop: 16,
    alignItems: 'center',
  },
  switchText: {
    color: '#38BDF8',
    fontSize: 14,
    fontWeight: '600',
  },
  dividerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginVertical: 14,
  },
  dividerLine: {
    flex: 1,
    height: 1,
    backgroundColor: '#334155',
  },
  dividerText: {
    color: '#64748B',
    fontSize: 10,
    fontWeight: '700',
    marginHorizontal: 10,
    letterSpacing: 1,
  },
  googleButton: {
    backgroundColor: '#FFFFFF',
    paddingVertical: 13,
    borderRadius: 10,
    alignItems: 'center',
    marginBottom: 6,
  },
  googleButtonText: {
    color: '#0F172A',
    fontWeight: '700',
    fontSize: 14,
  },
  biometricBadge: {
    color: '#38BDF8',
    fontSize: 11,
    textAlign: 'center',
    marginTop: 4,
    marginBottom: 6,
    fontWeight: '500',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(15, 23, 42, 0.85)',
    justifyContent: 'center',
    padding: 24,
  },
  modalCard: {
    backgroundColor: '#1E293B',
    borderRadius: 20,
    padding: 24,
    borderWidth: 1,
    borderColor: '#334155',
  },
  modalBadge: {
    color: '#38BDF8',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.5,
    marginBottom: 8,
  },
  modalTitle: {
    color: '#F8FAFC',
    fontSize: 20,
    fontWeight: '700',
    marginBottom: 6,
  },
  modalSubtitle: {
    color: '#94A3B8',
    fontSize: 13,
    lineHeight: 18,
    marginBottom: 18,
  },
  modalLabel: {
    color: '#CBD5E1',
    fontSize: 12,
    fontWeight: '600',
    marginBottom: 6,
  },
  modalInput: {
    backgroundColor: '#0F172A',
    color: '#F8FAFC',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 14,
    borderWidth: 1,
    borderColor: '#334155',
    marginBottom: 12,
  },
  modalPrimaryBtn: {
    backgroundColor: '#0284C7',
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: 'center',
    marginTop: 6,
    marginBottom: 10,
  },
  modalPrimaryBtnText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '700',
  },
  modalQuickBtn: {
    backgroundColor: 'rgba(56, 189, 248, 0.08)',
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.3)',
    borderRadius: 8,
    paddingVertical: 10,
    paddingHorizontal: 10,
    alignItems: 'center',
    marginBottom: 10,
  },
  modalQuickBtnText: {
    color: '#7DD3FC',
    fontSize: 12,
    fontWeight: '600',
    textAlign: 'center',
  },
  modalCancelBtn: {
    paddingVertical: 10,
    alignItems: 'center',
  },
  modalCancelBtnText: {
    color: '#94A3B8',
    fontSize: 13,
    fontWeight: '500',
  },
});
