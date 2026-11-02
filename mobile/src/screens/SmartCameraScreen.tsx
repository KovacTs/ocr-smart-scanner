import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Platform,
  StatusBar,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { Gyroscope } from 'expo-sensors';
import { apiClient } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { ScanItem } from '../types/models';

interface Props {
  onScanCompleted: (scan: ScanItem) => void;
  onNavigateHistory: () => void;
}

const STABILITY_TICKS_REQUIRED = 5; // ~1.7s para asegurar bloqueo físico del lente AF
const GYRO_STRONG_JERK_LIMIT = 1.1; // Detecta movimiento sin bloquear por pulso humano

export const SmartCameraScreen: React.FC<Props> = ({
  onScanCompleted,
  onNavigateHistory,
}) => {
  const { token } = useAuth();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView | null>(null);

  const [autoCapture, setAutoCapture] = useState<boolean>(true);
  const [torchOn, setTorchOn] = useState<boolean>(false);
  // Zoom 0.06 (~1.3x) por defecto: evita acercar el celular a menos de 15cm donde el lente pierde foco macro
  const [zoom, setZoom] = useState<number>(0.06);
  const [autofocusMode, setAutofocusMode] = useState<'on' | 'off'>('on');

  const [statusColor, setStatusColor] = useState<'RED' | 'YELLOW' | 'GREEN'>(
    'GREEN'
  );
  const [hudMessage, setHudMessage] = useState<string>(
    'Toque el recuadro para reenfocar • Mantenga a ~18cm'
  );
  const [blurScore, setBlurScore] = useState<number>(0);
  const [threshold, setThreshold] = useState<number>(380);
  const [stableTicks, setStableTicks] = useState<number>(0);
  const [isProcessingUpload, setIsProcessingUpload] = useState<boolean>(false);
  const [showForceButton, setShowForceButton] = useState<boolean>(false);

  const stableTicksRef = useRef<number>(0);
  const isCapturingRef = useRef<boolean>(false);
  const isCameraReadyRef = useRef<boolean>(false);
  const cooldownUntilRef = useRef<number>(0);
  const lastGyroMagnitudeRef = useRef<number>(0);
  const autoCaptureRef = useRef<boolean>(autoCapture);

  useEffect(() => {
    autoCaptureRef.current = autoCapture;
  }, [autoCapture]);

  /**
   * Fuerza al motor físico del lente de la cámara a re-escanear la distancia focal
   * alternando autofocus off -> on.
   */
  const triggerHardwareRefocus = useCallback(() => {
    setAutofocusMode('off');
    stableTicksRef.current = 0;
    setStableTicks(0);
    setStatusColor('YELLOW');
    setHudMessage('Ajustando lente de enfoque óptico...');
    setTimeout(() => {
      setAutofocusMode('on');
    }, 120);
  }, []);

  const executeSmartCapture = useCallback(
    async (forceCapture = false) => {
      if (
        !cameraRef.current ||
        !token ||
        isCapturingRef.current ||
        !isCameraReadyRef.current
      ) {
        return;
      }

      isCapturingRef.current = true;
      setIsProcessingUpload(true);
      setStatusColor('GREEN');
      setHudMessage('Mejorando contraste CLAHE y ejecutando OCR neuronal...');

      try {
        const photo = await cameraRef.current.takePictureAsync({
          quality: 0.95,
          shutterSound: false,
        });

        if (!photo?.uri) {
          throw new Error('No se pudo obtener la imagen de la cámara.');
        }

        const savedScan = await apiClient.uploadScanWebP(
          token,
          photo.uri,
          forceCapture
        );
        setShowForceButton(false);
        onScanCompleted(savedScan);
      } catch (err: any) {
        if (err.focusAnalysis) {
          const score = err.focusAnalysis.blur_score ?? 0;
          const thresh = err.focusAnalysis.threshold ?? 380;
          setBlurScore(score);
          setThreshold(thresh);
          setStatusColor('YELLOW');
          setShowForceButton(true);
          setHudMessage(
            `Foco insuficiente (${score.toFixed(0)}/${thresh.toFixed(
              0
            )}) — Aléjese a ~18cm o use 1.3x`
          );
          triggerHardwareRefocus();
          cooldownUntilRef.current = Date.now() + 1800;
        } else {
          setStatusColor('RED');
          setHudMessage(
            err.message || 'Error de conexión con el servidor OCR'
          );
          cooldownUntilRef.current = Date.now() + 2200;
          stableTicksRef.current = 0;
          setStableTicks(0);
          if (!autoCaptureRef.current) {
            Alert.alert('Aviso', err.message || 'No se pudo procesar la foto');
          }
        }
      } finally {
        isCapturingRef.current = false;
        setIsProcessingUpload(false);
      }
    },
    [token, onScanCompleted, triggerHardwareRefocus]
  );

  // 1. Giroscopio para detectar movimientos mientras el lente enfoca
  useEffect(() => {
    let sub: { remove: () => void } | null = null;
    try {
      Gyroscope.setUpdateInterval(140);
      sub = Gyroscope.addListener(({ x, y, z }) => {
        lastGyroMagnitudeRef.current = Math.sqrt(x * x + y * y + z * z);
      });
    } catch {
      // Continuar si el dispositivo no reporta giroscopio
    }
    return () => {
      if (sub) sub.remove();
    };
  }, []);

  // 2. Temporizador de bloqueo de enfoque y Auto-Captura
  useEffect(() => {
    if (!permission?.granted) return;

    const timer = setInterval(() => {
      if (isCapturingRef.current || !isCameraReadyRef.current) return;
      if (Date.now() < cooldownUntilRef.current) return;

      const isMoving =
        lastGyroMagnitudeRef.current > GYRO_STRONG_JERK_LIMIT;

      if (isMoving) {
        const dec = Math.max(0, stableTicksRef.current - 1);
        stableTicksRef.current = dec;
        setStableTicks(dec);
        setStatusColor('YELLOW');
        setHudMessage('Movimiento detectado — Mantenga firme para enfocar');
        return;
      }

      const nextTicks = Math.min(
        STABILITY_TICKS_REQUIRED,
        stableTicksRef.current + 1
      );
      stableTicksRef.current = nextTicks;
      setStableTicks(nextTicks);

      if (nextTicks < STABILITY_TICKS_REQUIRED) {
        setStatusColor('GREEN');
        setHudMessage(
          `Enfocando caracteres (${nextTicks}/${STABILITY_TICKS_REQUIRED})...`
        );
      } else {
        setStatusColor('GREEN');
        setHudMessage('¡Enfoque bloqueado! Capturando en alta definición...');
        if (autoCaptureRef.current && !isCapturingRef.current) {
          stableTicksRef.current = 0;
          setStableTicks(0);
          executeSmartCapture(false);
        }
      }
    }, 350);

    return () => clearInterval(timer);
  }, [permission?.granted, executeSmartCapture]);

  if (!permission) {
    return <View style={styles.center} />;
  }

  if (!permission.granted) {
    return (
      <View style={styles.center}>
        <Text style={styles.permText}>
          Necesitamos permiso de cámara para escanear tus facturas y boletas.
        </Text>
        <TouchableOpacity style={styles.permBtn} onPress={requestPermission}>
          <Text style={styles.permBtnText}>Habilitar Cámara</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const borderColor =
    statusColor === 'GREEN'
      ? '#22C55E'
      : statusColor === 'YELLOW'
      ? '#EAB308'
      : '#EF4444';

  return (
    <View style={styles.container}>
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing="back"
        autofocus={autofocusMode}
        zoom={zoom}
        enableTorch={torchOn}
        animateShutter={false}
        onCameraReady={() => {
          isCameraReadyRef.current = true;
        }}
      />

      {/* Barra Superior */}
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.navBtn} onPress={onNavigateHistory}>
          <Text style={styles.navBtnText}>← Historial</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.torchBtn, torchOn && styles.torchBtnActive]}
          onPress={() => setTorchOn(!torchOn)}
        >
          <Text style={styles.torchBtnText}>
            {torchOn ? 'Luz: ON' : 'Luz: OFF'}
          </Text>
        </TouchableOpacity>

        <View style={styles.autoSwitchRow}>
          <Text style={styles.autoLabel}>Auto</Text>
          <Switch
            value={autoCapture}
            onValueChange={(val) => {
              setAutoCapture(val);
              stableTicksRef.current = 0;
              setStableTicks(0);
            }}
            trackColor={{ false: '#334155', true: '#0284C7' }}
          />
        </View>
      </View>

      {/* Marco ROI: Tocar el recuadro fuerza el reenfoque físico del lente */}
      <TouchableOpacity
        activeOpacity={0.9}
        onPress={triggerHardwareRefocus}
        style={styles.roiWrapper}
      >
        <View style={[styles.roiFrame, { borderColor }]}>
          <View style={[styles.statusPill, { backgroundColor: borderColor }]}>
            <Text style={styles.statusPillText}>{hudMessage}</Text>
          </View>
        </View>
      </TouchableOpacity>

      {/* Selector de Zoom Macro para Boletas (Evita desenfoque por acercar demasiado el lente) */}
      <View style={styles.zoomBar}>
        {[
          { label: '1.0x Normal', val: 0 },
          { label: '1.3x Boletas (Óptimo)', val: 0.06 },
          { label: '1.6x Detalle', val: 0.12 },
        ].map((opt) => {
          const active = Math.abs(zoom - opt.val) < 0.01;
          return (
            <TouchableOpacity
              key={opt.label}
              style={[styles.zoomPill, active && styles.zoomPillActive]}
              onPress={() => {
                setZoom(opt.val);
                triggerHardwareRefocus();
              }}
            >
              <Text
                style={[
                  styles.zoomPillText,
                  active && styles.zoomPillTextActive,
                ]}
              >
                {opt.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>

      {/* Panel Inferior de Telemetría y Disparo */}
      <View style={styles.bottomHud}>
        <View style={styles.metricsRow}>
          <Text style={styles.metricItem}>
            Nitidez:{' '}
            {blurScore > 0
              ? `${blurScore.toFixed(0)} (Mín: ${threshold.toFixed(0)})`
              : 'PP-OCRv4 + CLAHE'}
          </Text>
          <Text style={styles.metricItem}>
            Foco AF: {stableTicks}/{STABILITY_TICKS_REQUIRED}
          </Text>
        </View>

        {isProcessingUpload ? (
          <View style={styles.processingBox}>
            <ActivityIndicator size="large" color="#38BDF8" />
            <Text style={styles.processingText}>
              Aplicando filtro CLAHE, verificando nitidez y leyendo caracteres...
            </Text>
          </View>
        ) : (
          <View style={styles.buttonsColumn}>
            <TouchableOpacity
              style={styles.shutterButton}
              onPress={() => executeSmartCapture(false)}
            >
              <Text style={styles.shutterText}>
                Capturar Ahora (Con Validación de Enfoque)
              </Text>
            </TouchableOpacity>

            {showForceButton && (
              <TouchableOpacity
                style={styles.forceButton}
                onPress={() => executeSmartCapture(true)}
              >
                <Text style={styles.forceButtonText}>
                  Forzar Captura (Boleta con tinta tenue)
                </Text>
              </TouchableOpacity>
            )}
          </View>
        )}
      </View>
    </View>
  );
};

const topPad =
  Platform.OS === 'android' ? (StatusBar.currentHeight || 24) + 10 : 48;

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },
  center: {
    flex: 1,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  permText: {
    color: '#F8FAFC',
    fontSize: 16,
    textAlign: 'center',
    marginBottom: 18,
  },
  permBtn: {
    backgroundColor: '#0284C7',
    paddingHorizontal: 24,
    paddingVertical: 14,
    borderRadius: 10,
  },
  permBtnText: { color: '#FFF', fontWeight: '700', fontSize: 15 },
  topBar: {
    marginTop: topPad,
    marginHorizontal: 16,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: 'rgba(15, 23, 42, 0.88)',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 12,
  },
  navBtn: {
    backgroundColor: '#0284C7',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
  },
  navBtnText: { color: '#FFF', fontWeight: '700', fontSize: 13 },
  torchBtn: {
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#475569',
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 8,
  },
  torchBtnActive: {
    backgroundColor: '#EAB308',
    borderColor: '#FACC15',
  },
  torchBtnText: {
    color: '#F8FAFC',
    fontWeight: '700',
    fontSize: 12,
  },
  autoSwitchRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  autoLabel: { color: '#E2E8F0', fontSize: 13, fontWeight: '600' },
  roiWrapper: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 22,
  },
  roiFrame: {
    width: '100%',
    height: '76%',
    borderWidth: 3,
    borderRadius: 18,
    justifyContent: 'flex-start',
    alignItems: 'center',
    paddingTop: 12,
  },
  statusPill: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: 20,
    marginHorizontal: 10,
  },
  statusPillText: {
    color: '#0F172A',
    fontWeight: '800',
    fontSize: 12,
    textAlign: 'center',
  },
  zoomBar: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 8,
    marginBottom: 10,
    paddingHorizontal: 16,
  },
  zoomPill: {
    backgroundColor: 'rgba(15, 23, 42, 0.85)',
    borderWidth: 1,
    borderColor: '#475569',
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 18,
  },
  zoomPillActive: {
    backgroundColor: '#0284C7',
    borderColor: '#38BDF8',
  },
  zoomPillText: {
    color: '#CBD5E1',
    fontSize: 12,
    fontWeight: '700',
  },
  zoomPillTextActive: {
    color: '#FFFFFF',
  },
  bottomHud: {
    backgroundColor: 'rgba(15, 23, 42, 0.94)',
    paddingHorizontal: 20,
    paddingTop: 14,
    paddingBottom: 30,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
  },
  metricsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  metricItem: { color: '#94A3B8', fontSize: 12, fontWeight: '600' },
  buttonsColumn: { gap: 10 },
  shutterButton: {
    backgroundColor: '#22C55E',
    paddingVertical: 15,
    borderRadius: 12,
    alignItems: 'center',
  },
  shutterText: {
    color: '#FFFFFF',
    fontWeight: '800',
    fontSize: 15,
  },
  forceButton: {
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#38BDF8',
    paddingVertical: 11,
    borderRadius: 10,
    alignItems: 'center',
  },
  forceButtonText: {
    color: '#38BDF8',
    fontWeight: '700',
    fontSize: 13,
  },
  processingBox: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    paddingVertical: 10,
  },
  processingText: {
    color: '#E2E8F0',
    fontSize: 13,
    fontWeight: '600',
    flex: 1,
  },
});
