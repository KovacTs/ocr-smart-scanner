import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Image,
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
import * as Haptics from 'expo-haptics';
import { apiClient } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { offlineQueue } from '../services/offlineQueue';
import { ScanItem } from '../types/models';

interface Props {
  onScanCompleted: (scan: ScanItem) => void;
  onNavigateHistory: () => void;
}

const STABILITY_TICKS_REQUIRED = 3; // ~1.0s de estabilidad para validar enfoque óptimo rápido
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
  const [threshold, setThreshold] = useState<number>(240);
  const [stableTicks, setStableTicks] = useState<number>(0);
  const [isEvaluatingFocus, setIsEvaluatingFocus] = useState<boolean>(false);
  const [isProcessingUpload, setIsProcessingUpload] = useState<boolean>(false);
  const [showForceButton, setShowForceButton] = useState<boolean>(false);
  const [detectedTedLabel, setDetectedTedLabel] = useState<string | null>(null);

  // Estado de congelamiento instantáneo ("Freeze & Release")
  const [capturedPreviewUri, setCapturedPreviewUri] = useState<string | null>(
    null
  );
  const [capturedBlurScore, setCapturedBlurScore] = useState<
    number | undefined
  >(undefined);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const stableTicksRef = useRef<number>(0);
  const isCapturingRef = useRef<boolean>(false);
  const isCameraReadyRef = useRef<boolean>(false);
  const cooldownUntilRef = useRef<number>(0);
  const lastGyroMagnitudeRef = useRef<number>(0);
  const autoCaptureRef = useRef<boolean>(autoCapture);
  const detectedTedXmlRef = useRef<string>('');
  const capturedPreviewUriRef = useRef<string | null>(null);
  const focusAttemptsRef = useRef<number>(0);
  const consecutiveErrorsRef = useRef<number>(0);

  useEffect(() => {
    autoCaptureRef.current = autoCapture;
  }, [autoCapture]);

  useEffect(() => {
    capturedPreviewUriRef.current = capturedPreviewUri;
  }, [capturedPreviewUri]);

  /**
   * Detecta en vivo el Timbre Electrónico SII (PDF417 <TED>) o código de barras SII de 30-31 dígitos
   */
  const handleBarcodeScanned = useCallback(
    (result: { type: string; data: string }) => {
      const raw = (result?.data || '').trim();
      if (!raw || detectedTedXmlRef.current === raw) return;

      const upper = raw.toUpperCase();
      if (
        upper.includes('<TED') ||
        (upper.includes('<DD') && upper.includes('<RE>') && upper.includes('<F>'))
      ) {
        detectedTedXmlRef.current = raw;
        const mFolio = raw.match(/<F>([^<]+)<\/F>/i);
        const mRs = raw.match(/<RS>([^<]+)<\/RS>/i);
        const folioTxt = mFolio ? mFolio[1].trim() : 'OK';
        const rsTxt = mRs ? mRs[1].trim().slice(0, 22) : 'SII';
        setDetectedTedLabel(`✓ Timbre SII PDF417: Folio #${folioTxt} (${rsTxt})`);
      } else if (/^20\d{6}\d{11,12}\d{10}\d$/.test(raw)) {
        detectedTedXmlRef.current = raw;
        const folio10 = raw.slice(-11, -1);
        setDetectedTedLabel(`✓ Código SII Detectado: Folio #${folio10}`);
      }
    },
    []
  );

  /**
   * Fuerza al motor físico del lente de la cámara a re-escanear la distancia focal
   * alternando autofocus off -> on.
   */
  const triggerHardwareRefocus = useCallback((updateHud = true) => {
    if (capturedPreviewUriRef.current) return;
    setAutofocusMode('off');
    stableTicksRef.current = 0;
    setStableTicks(0);
    if (updateHud) {
      setStatusColor('YELLOW');
      setHudMessage('Ajustando lente de enfoque óptico...');
    }
    setTimeout(() => {
      setAutofocusMode('on');
    }, 120);
  }, []);

  /**
   * Descarta la imagen congelada y reactiva la cámara en vivo
   */
  const discardCapturedPreview = useCallback(() => {
    isCapturingRef.current = false;
    setIsProcessingUpload(false);
    setIsEvaluatingFocus(false);
    setUploadError(null);
    setCapturedPreviewUri(null);
    setCapturedBlurScore(undefined);
    stableTicksRef.current = 0;
    setStableTicks(0);
    focusAttemptsRef.current = 0;
    consecutiveErrorsRef.current = 0;
    setStatusColor('GREEN');
    setHudMessage('Toque el recuadro para reenfocar • Mantenga a ~18cm');
    cooldownUntilRef.current = Date.now() + 800;
  }, []);

  /**
   * Fase C: Ejecuta el análisis OCR + Timbre SII + LLM sobre la foto YA congelada,
   * con la cámara apagada para que el usuario pueda bajar el teléfono cómodamente.
   */
  const analyzeCapturedPhoto = useCallback(
    async (photoUri: string, validatedScore?: number) => {
      if (!token) return;

      isCapturingRef.current = true;
      setIsProcessingUpload(true);
      setUploadError(null);
      setStatusColor('GREEN');
      setHudMessage(
        '✓ ¡Boleta capturada! Ya puedes bajar el teléfono mientras analizamos'
      );

      try {
        const savedScan = await apiClient.uploadScanWebP(
          token,
          photoUri,
          true,
          detectedTedXmlRef.current || undefined,
          validatedScore
        );
        setShowForceButton(false);
        if (Platform.OS !== 'web') {
          Haptics.notificationAsync(
            Haptics.NotificationFeedbackType.Success
          ).catch(() => {});
        }
        onScanCompleted(savedScan);
      } catch (err: any) {
        if (Platform.OS !== 'web') {
          Haptics.notificationAsync(
            Haptics.NotificationFeedbackType.Error
          ).catch(() => {});
        }
        const msg =
          err.message || 'Error de conexión con el servidor OCR';
        setStatusColor('RED');
        setUploadError(msg);
        setHudMessage(
          'Error al enviar la captura — Puedes reintentar o guardar sin conexión'
        );
      } finally {
        isCapturingRef.current = false;
        setIsProcessingUpload(false);
      }
    },
    [token, onScanCompleted]
  );

  /**
   * Guarda la imagen congelada en la cola offline local (Fase 2)
   */
  const handleSaveToOfflineQueue = useCallback(async () => {
    if (!capturedPreviewUri) return;
    try {
      await offlineQueue.enqueue({
        photoUri: capturedPreviewUri,
        siiTedXml: detectedTedXmlRef.current || undefined,
        precomputedBlurScore: capturedBlurScore,
      });
      if (Platform.OS !== 'web') {
        Haptics.notificationAsync(
          Haptics.NotificationFeedbackType.Success
        ).catch(() => {});
      }
      Alert.alert(
        'Guardado en Cola Offline',
        'El comprobante se guardó localmente en tu dispositivo. Podrás sincronizarlo con un toque desde el Historial cuando recuperes señal.',
        [
          {
            text: 'Ir al Historial',
            onPress: () => {
              discardCapturedPreview();
              onNavigateHistory();
            },
          },
        ]
      );
    } catch (e: any) {
      Alert.alert('Error', e.message || 'No se pudo guardar localmente.');
    }
  }, [
    capturedPreviewUri,
    capturedBlurScore,
    discardCapturedPreview,
    onNavigateHistory,
  ]);

  /**
   * Flujo en 3 fases:
   * 1. Toma frame cuando el pulso está estable.
   * 2. Evalúa nitidez del texto en /evaluate-focus (~100ms). Si tras reintentar hay documento encuadrado, captura sin quedar en bucle.
   * 3. Apenas el enfoque es aceptado, CONGELA la foto en pantalla, APAGA la cámara/flash y lanza el OCR.
   */
  const executeSmartCapture = useCallback(
    async (forceCapture = false) => {
      if (
        !cameraRef.current ||
        !token ||
        isCapturingRef.current ||
        !isCameraReadyRef.current ||
        capturedPreviewUriRef.current
      ) {
        return;
      }

      isCapturingRef.current = true;
      setIsEvaluatingFocus(true);
      setStatusColor('GREEN');
      setHudMessage(
        forceCapture
          ? 'Capturando comprobante...'
          : 'Verificando enfoque óptimo del texto...'
      );

      try {
        const photo = await cameraRef.current.takePictureAsync({
          quality: 0.95,
          shutterSound: false,
        });

        if (!photo?.uri) {
          throw new Error('No se pudo obtener la imagen de la cámara.');
        }

        let validatedScore: number | undefined = undefined;

        // Fase A: Evaluación rápida de enfoque antes de congelar y antes del OCR pesado
        if (!forceCapture && !detectedTedXmlRef.current) {
          const focusCheck = await apiClient.evaluateFrameFocus(
            token,
            photo.uri
          );
          consecutiveErrorsRef.current = 0;
          setBlurScore(focusCheck.blur_score);
          setThreshold(focusCheck.threshold);
          validatedScore = focusCheck.blur_score;

          if (!focusCheck.document_detected) {
            focusAttemptsRef.current = 0;
            setStatusColor('RED');
            setHudMessage(
              focusCheck.message || 'Apunte al texto de la boleta o factura'
            );
            cooldownUntilRef.current = Date.now() + 900;
            stableTicksRef.current = 0;
            setStableTicks(0);
            isCapturingRef.current = false;
            setIsEvaluatingFocus(false);
            return;
          }

          focusAttemptsRef.current += 1;
          const attempt = focusAttemptsRef.current;
          // Aceptación progresiva: en el intento 1 exige el umbral completo; en el intento 2 acepta >=50% del umbral;
          // en el intento 3 captura automáticamente si el documento está encuadrado para jamás quedar en bucle infinito.
          const isAcceptableFocus =
            focusCheck.is_focused ||
            (attempt >= 2 &&
              focusCheck.blur_score >= focusCheck.threshold * 0.5) ||
            attempt >= 3;

          if (!isAcceptableFocus) {
            setStatusColor('YELLOW');
            setShowForceButton(true);
            setHudMessage(
              `Reenfocando (${focusCheck.blur_score.toFixed(
                0
              )}/${focusCheck.threshold.toFixed(
                0
              )}) — Mantenga firme un instante...`
            );
            triggerHardwareRefocus(false);
            cooldownUntilRef.current = Date.now() + 950;
            isCapturingRef.current = false;
            setIsEvaluatingFocus(false);
            return;
          }
        }

        // Fase B: ¡Enfoque óptimo confirmado! Congelar imagen en pantalla y apagar cámara + linterna
        focusAttemptsRef.current = 0;
        consecutiveErrorsRef.current = 0;
        if (Platform.OS !== 'web') {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
        }
        setIsEvaluatingFocus(false);
        isCameraReadyRef.current = false;
        setTorchOn(false);
        setCapturedBlurScore(validatedScore);
        setCapturedPreviewUri(photo.uri);

        // Fase C: Ejecutar análisis OCR pesado mientras el usuario ya descansa la mano
        await analyzeCapturedPhoto(photo.uri, validatedScore);
      } catch (err: any) {
        setIsEvaluatingFocus(false);
        isCapturingRef.current = false;
        consecutiveErrorsRef.current += 1;
        setStatusColor('RED');
        setShowForceButton(true);
        setHudMessage(
          err.message || 'Error al verificar enfoque de la cámara'
        );
        cooldownUntilRef.current = Date.now() + 2200;
        stableTicksRef.current = 0;
        setStableTicks(0);
        // Si falla 2 veces seguidas la verificación (ej. problema de red), pausar Auto para evitar bucle
        if (consecutiveErrorsRef.current >= 2 && autoCaptureRef.current) {
          setAutoCapture(false);
          autoCaptureRef.current = false;
        }
        if (!autoCaptureRef.current) {
          Alert.alert('Aviso', err.message || 'No se pudo capturar la foto');
        }
      }
    },
    [token, triggerHardwareRefocus, analyzeCapturedPhoto]
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
      if (
        isCapturingRef.current ||
        !isCameraReadyRef.current ||
        capturedPreviewUriRef.current
      ) {
        return;
      }
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

      const requiredTicks = detectedTedXmlRef.current
        ? 2
        : STABILITY_TICKS_REQUIRED;

      const nextTicks = Math.min(
        requiredTicks,
        stableTicksRef.current + 1
      );
      stableTicksRef.current = nextTicks;
      setStableTicks(nextTicks);

      if (nextTicks < requiredTicks) {
        setStatusColor('GREEN');
        setHudMessage(
          `Enfocando caracteres (${nextTicks}/${requiredTicks})...`
        );
      } else {
        setStatusColor('GREEN');
        setHudMessage('¡Pulso estable! Evaluando nitidez óptima...');
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
      {capturedPreviewUri ? (
        <Image
          source={{ uri: capturedPreviewUri }}
          style={StyleSheet.absoluteFill}
          resizeMode="cover"
        />
      ) : (
        <CameraView
          ref={cameraRef}
          style={StyleSheet.absoluteFill}
          facing="back"
          autofocus={autofocusMode}
          zoom={zoom}
          enableTorch={torchOn}
          animateShutter={false}
          barcodeScannerSettings={{
            barcodeTypes: ['pdf417', 'qr', 'code128'],
          }}
          onBarcodeScanned={handleBarcodeScanned}
          onCameraReady={() => {
            isCameraReadyRef.current = true;
          }}
        />
      )}

      {/* Barra Superior */}
      <View style={styles.topBar}>
        <TouchableOpacity
          style={styles.navBtn}
          onPress={onNavigateHistory}
          disabled={isProcessingUpload}
        >
          <Text style={styles.navBtnText}>← Historial</Text>
        </TouchableOpacity>

        {capturedPreviewUri ? (
          <View style={styles.cameraOffBadge}>
            <Text style={styles.cameraOffBadgeText}>
              📷 Cámara apagada • Imagen fija
            </Text>
          </View>
        ) : (
          <>
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
                  focusAttemptsRef.current = 0;
                  consecutiveErrorsRef.current = 0;
                }}
                trackColor={{ false: '#334155', true: '#0284C7' }}
              />
            </View>
          </>
        )}
      </View>

      {/* Marco ROI: Tocar el recuadro fuerza el reenfoque físico del lente */}
      <TouchableOpacity
        activeOpacity={0.9}
        onPress={() => triggerHardwareRefocus(true)}
        disabled={Boolean(capturedPreviewUri)}
        style={styles.roiWrapper}
      >
        <View style={[styles.roiFrame, { borderColor }]}>
          <View style={[styles.statusPill, { backgroundColor: borderColor }]}>
            <Text style={styles.statusPillText}>{hudMessage}</Text>
          </View>
          {detectedTedLabel && (
            <View style={styles.tedBadge}>
              <Text style={styles.tedBadgeText}>{detectedTedLabel}</Text>
            </View>
          )}
          {capturedPreviewUri && !uploadError && (
            <View style={styles.freezeReleaseBanner}>
              <Text style={styles.freezeReleaseTitle}>
                ✓ ¡Enfoque óptimo capturado!
              </Text>
              <Text style={styles.freezeReleaseSubtitle}>
                Ya puedes retirar la boleta o bajar el teléfono mientras
                extraemos los datos con el OCR.
              </Text>
            </View>
          )}
        </View>
      </TouchableOpacity>

      {/* Selector de Zoom Macro para Boletas (Solo visible cuando la cámara está activa) */}
      {!capturedPreviewUri && (
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
                  triggerHardwareRefocus(true);
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
      )}

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
            {capturedPreviewUri
              ? 'Estado: Captura Congelada'
              : `Foco AF: ${stableTicks}/${STABILITY_TICKS_REQUIRED}`}
          </Text>
        </View>

        {isProcessingUpload ? (
          <View style={styles.processingBox}>
            <ActivityIndicator size="large" color="#22C55E" />
            <Text style={styles.processingText}>
              {detectedTedXmlRef.current
                ? 'Imagen congelada • Validando Timbre SII y extrayendo datos...'
                : 'Imagen congelada • Aplicando filtro CLAHE y ejecutando OCR + IA...'}
            </Text>
          </View>
        ) : capturedPreviewUri && uploadError ? (
          <View style={styles.buttonsColumn}>
            <TouchableOpacity
              style={styles.shutterButton}
              onPress={() =>
                analyzeCapturedPhoto(capturedPreviewUri, capturedBlurScore)
              }
            >
              <Text style={styles.shutterText}>
                ↻ Reintentar análisis con esta misma foto
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.offlineSaveBtn}
              onPress={handleSaveToOfflineQueue}
            >
              <Text style={styles.offlineSaveBtnText}>
                💾 Guardar en cola sin conexión (Modo Offline)
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.forceButton}
              onPress={discardCapturedPreview}
            >
              <Text style={styles.forceButtonText}>
                Descartar y volver a la cámara
              </Text>
            </TouchableOpacity>
          </View>
        ) : (
          <View style={styles.buttonsColumn}>
            <TouchableOpacity
              style={styles.shutterButton}
              disabled={isEvaluatingFocus}
              onPress={() => executeSmartCapture(true)}
            >
              <Text style={styles.shutterText}>
                {isEvaluatingFocus
                  ? 'Procesando captura...'
                  : '📸 Capturar Ahora'}
              </Text>
            </TouchableOpacity>

            {showForceButton && (
              <TouchableOpacity
                style={styles.forceButton}
                disabled={isEvaluatingFocus}
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
  cameraOffBadge: {
    backgroundColor: 'rgba(34, 197, 94, 0.2)',
    borderWidth: 1,
    borderColor: '#22C55E',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
  },
  cameraOffBadgeText: {
    color: '#4ADE80',
    fontWeight: '700',
    fontSize: 12,
  },
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
  tedBadge: {
    marginTop: 8,
    backgroundColor: 'rgba(16, 185, 129, 0.95)',
    borderWidth: 1,
    borderColor: '#A7F3D0',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    marginHorizontal: 10,
  },
  tedBadgeText: {
    color: '#064E3B',
    fontWeight: '800',
    fontSize: 12,
    textAlign: 'center',
  },
  freezeReleaseBanner: {
    marginTop: 'auto',
    marginBottom: 18,
    marginHorizontal: 14,
    backgroundColor: 'rgba(15, 23, 42, 0.92)',
    borderWidth: 2,
    borderColor: '#22C55E',
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 14,
    alignItems: 'center',
  },
  freezeReleaseTitle: {
    color: '#4ADE80',
    fontWeight: '800',
    fontSize: 15,
    marginBottom: 4,
    textAlign: 'center',
  },
  freezeReleaseSubtitle: {
    color: '#F8FAFC',
    fontWeight: '600',
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 18,
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
  offlineSaveBtn: {
    backgroundColor: '#0284C7',
    paddingVertical: 12,
    borderRadius: 10,
    alignItems: 'center',
  },
  offlineSaveBtnText: {
    color: '#FFFFFF',
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
