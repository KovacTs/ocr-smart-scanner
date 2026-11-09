import Constants from 'expo-constants';
import * as ImageManipulator from 'expo-image-manipulator';
import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import {
  AuthResponse,
  FocusCheckResult,
  NormalizedDocumentData,
  ScanItem,
  ScanStatusItem,
} from '../types/models';

const PRIVATE_LAN_REGEX =
  /^https?:\/\/(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?/i;

/**
 * Extrae automáticamente la IP WiFi actual del computador donde corre Expo Metro Bundler.
 * Así, aunque cambies de red WiFi (casa, oficina, hotspot del celular), la app detecta
 * la nueva IP sin tener que editar manualmente el archivo .env.
 */
function getDetectedMetroHostIp(): string | null {
  const rawHost =
    Constants.expoConfig?.hostUri ||
    (Constants.manifest2 as any)?.extra?.expoClient?.hostUri ||
    (Constants.manifest2 as any)?.extra?.expoGo?.debuggerHost ||
    (Constants as any)?.experienceUrl ||
    '';

  if (!rawHost) return null;

  const cleaned = String(rawHost)
    .replace(/^exp:\/\//i, '')
    .replace(/^https?:\/\//i, '')
    .split('/')[0]
    .split(':')[0]
    .trim();

  if (
    cleaned &&
    cleaned !== 'localhost' &&
    cleaned !== '127.0.0.1' &&
    /^\d{1,3}(\.\d{1,3}){3}$/.test(cleaned)
  ) {
    return cleaned;
  }
  return null;
}

function resolveDefaultApiBaseUrl(): string {
  const envUrl = (process.env.EXPO_PUBLIC_API_URL || '').trim();
  const metroIp = getDetectedMetroHostIp();

  // Si hay una URL de nube pública (ej. Oracle Cloud / dominio HTTPS), respetarla
  if (
    envUrl &&
    envUrl.toUpperCase() !== 'AUTO' &&
    !PRIVATE_LAN_REGEX.test(envUrl)
  ) {
    return envUrl.replace(/\/+$/, '');
  }

  // Si estamos en red local WiFi y Expo reporta la IP actual del equipo, usarla automáticamente
  if (metroIp) {
    return `http://${metroIp}:8000/api`;
  }

  // Si el usuario definió una IP LAN en .env y no hay hostUri de Metro, usar la del .env
  if (envUrl && envUrl.toUpperCase() !== 'AUTO') {
    return envUrl.replace(/\/+$/, '');
  }

  // Fallback a la IP LAN actual detectada en el equipo
  return 'http://192.168.1.168:8000/api';
}

let currentApiBaseUrl = resolveDefaultApiBaseUrl();

export function getApiBaseUrl(): string {
  return currentApiBaseUrl;
}

export function setCustomApiBaseUrl(newUrl: string): string {
  const trimmed = newUrl.trim().replace(/\/+$/, '');
  if (!trimmed || trimmed.toUpperCase() === 'AUTO') {
    currentApiBaseUrl = resolveDefaultApiBaseUrl();
  } else {
    currentApiBaseUrl = trimmed.endsWith('/api') ? trimmed : `${trimmed}/api`;
  }
  return currentApiBaseUrl;
}

/**
 * Comprime la foto en el dispositivo antes de subirla:
 * 1. Normaliza resolución (máximo 1800px de ancho).
 * 2. Codifica a formato WebP con calidad 88% (~90% reducción de peso).
 */
export async function compressToWebPClientSide(
  imageUri: string
): Promise<{ uri: string; width: number; height: number }> {
  const result = await ImageManipulator.manipulateAsync(
    imageUri,
    [{ resize: { width: 1800 } }],
    {
      compress: 0.88,
      format: ImageManipulator.SaveFormat.WEBP,
    }
  );
  return result;
}

export const apiClient = {
  async checkServerHealth(): Promise<{
    ok: boolean;
    url: string;
    message: string;
  }> {
    const baseUrl = getApiBaseUrl();
    const healthUrl = `${baseUrl.replace(/\/api$/, '')}/health`;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3500);
    try {
      const res = await fetch(healthUrl, {
        method: 'GET',
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      if (res.ok) {
        return {
          ok: true,
          url: baseUrl,
          message: 'Servidor Backend Conectado',
        };
      }
      return {
        ok: false,
        url: baseUrl,
        message: `Respuesta inesperada (HTTP ${res.status})`,
      };
    } catch {
      clearTimeout(timeoutId);
      return {
        ok: false,
        url: baseUrl,
        message: 'Sin conexión con el backend en esta red WiFi',
      };
    }
  },

  async register(
    email: string,
    password: string,
    fullName: string
  ): Promise<AuthResponse> {
    const baseUrl = getApiBaseUrl();
    let res: Response;
    try {
      res = await fetch(`${baseUrl}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, full_name: fullName }),
      });
    } catch {
      throw new Error(
        `No se pudo conectar al servidor (${baseUrl}). Verifica que el teléfono y el computador estén en el mismo WiFi.`
      );
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Error al registrar usuario');
    }
    return res.json();
  },

  async login(email: string, password: string): Promise<AuthResponse> {
    const baseUrl = getApiBaseUrl();
    let res: Response;
    try {
      res = await fetch(`${baseUrl}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
    } catch {
      throw new Error(
        `No se pudo conectar al servidor (${baseUrl}). Verifica que el teléfono y el computador estén en el mismo WiFi.`
      );
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Credenciales inválidas');
    }
    return res.json();
  },

  async loginWithGoogle(idToken: string): Promise<AuthResponse> {
    const baseUrl = getApiBaseUrl();
    let res: Response;
    try {
      res = await fetch(`${baseUrl}/auth/google`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: idToken }),
      });
    } catch {
      throw new Error(
        `No se pudo conectar al servidor (${baseUrl}). Verifica tu conexión de red.`
      );
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Error al autenticar con Google');
    }
    return res.json();
  },

  async refreshAccessToken(refreshToken: string): Promise<AuthResponse> {
    const res = await fetch(`${getApiBaseUrl()}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) {
      throw new Error('La sesión ha expirado. Inicie sesión nuevamente.');
    }
    return res.json();
  },

  async evaluateFrameFocus(
    token: string,
    frameUri: string
  ): Promise<FocusCheckResult> {
    const lightFrame = await ImageManipulator.manipulateAsync(
      frameUri,
      [{ resize: { width: 960 } }],
      {
        compress: 0.78,
        format: ImageManipulator.SaveFormat.WEBP,
      }
    );
    const formData = new FormData();
    const frameFile = new File(lightFrame.uri);
    formData.append('file', frameFile);

    const res = await fetch(`${getApiBaseUrl()}/scans/evaluate-focus`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
    });
    if (!res.ok) {
      throw new Error('Error evaluando enfoque del cuadro');
    }
    return res.json();
  },

  async uploadScanWebP(
    token: string,
    rawPhotoUri: string,
    forceCapture = false,
    siiTedXml?: string,
    precomputedBlurScore?: number,
    asyncMode = false
  ): Promise<ScanItem> {
    const compressed = await compressToWebPClientSide(rawPhotoUri);
    const formData = new FormData();
    const webpFile = new File(compressed.uri);
    formData.append('file', webpFile);
    formData.append('force_capture', String(forceCapture));
    if (asyncMode) {
      formData.append('async_mode', 'true');
    }
    if (siiTedXml && siiTedXml.trim()) {
      formData.append('sii_ted_xml', siiTedXml.trim());
    }
    if (
      typeof precomputedBlurScore === 'number' &&
      !Number.isNaN(precomputedBlurScore)
    ) {
      formData.append('precomputed_blur_score', String(precomputedBlurScore));
    }

    const res = await fetch(`${getApiBaseUrl()}/scans`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const msg =
        err.detail?.message ||
        (typeof err.detail === 'string' ? err.detail : null) ||
        'Imagen desenfocada. Mantenga firme la cámara.';
      const customErr: any = new Error(msg);
      customErr.focusAnalysis = err.detail?.focus_analysis || null;
      customErr.status = res.status;
      throw customErr;
    }
    return res.json();
  },

  async getScanStatus(token: string, scanId: string): Promise<ScanStatusItem> {
    const res = await fetch(`${getApiBaseUrl()}/scans/${scanId}/status`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error('Error al consultar estado del escaneo');
    return res.json();
  },

  async listScans(
    token: string,
    params?: {
      search?: string;
      document_type?: string;
      only_deleted?: boolean;
    }
  ): Promise<ScanItem[]> {
    const query = new URLSearchParams();
    if (params?.search) query.set('search', params.search);
    if (params?.document_type) query.set('document_type', params.document_type);
    if (params?.only_deleted) query.set('only_deleted', 'true');
    const qs = query.toString() ? `?${query.toString()}` : '';

    const res = await fetch(`${getApiBaseUrl()}/scans${qs}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error('Error cargando historial de escaneos');
    return res.json();
  },

  async updateScanVersion(
    token: string,
    scanId: string,
    normalizedData: NormalizedDocumentData,
    rawText?: string
  ): Promise<ScanItem> {
    const res = await fetch(`${getApiBaseUrl()}/scans/${scanId}`, {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        normalized_data: normalizedData,
        raw_text: rawText,
      }),
    });
    if (!res.ok) throw new Error('No se pudo guardar la nueva versión');
    return res.json();
  },

  async downloadAndShareExcel(
    token: string,
    scanIds?: string[]
  ): Promise<void> {
    const res = await fetch(`${getApiBaseUrl()}/scans/export-batch`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ scan_ids: scanIds || null }),
    });
    if (!res.ok) throw new Error('Error generando archivo Excel');

    const arrayBuffer = await res.arrayBuffer();
    const targetFile = new File(
      Paths.cache,
      `reporte_ocr_${Date.now()}.xlsx`
    );
    targetFile.create({ overwrite: true });
    await targetFile.write(new Uint8Array(arrayBuffer));

    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(targetFile.uri, {
        mimeType:
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        dialogTitle: 'Exportar Escaneos OCR a Excel (.xlsx)',
      });
    }
  },

  async deleteScan(
    token: string,
    scanId: string,
    permanent = false
  ): Promise<void> {
    const qs = permanent ? '?permanent=true' : '';
    const res = await fetch(`${getApiBaseUrl()}/scans/${scanId}${qs}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      throw new Error('No se pudo eliminar el escaneo');
    }
  },

  async restoreScan(token: string, scanId: string): Promise<ScanItem> {
    const res = await fetch(`${getApiBaseUrl()}/scans/${scanId}/restore`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      throw new Error('No se pudo restaurar el comprobante');
    }
    return res.json();
  },

  async deleteScansBatch(
    token: string,
    scanIds: string[],
    permanent = false
  ): Promise<{ deleted_count: number; soft_deleted: boolean }> {
    const res = await fetch(`${getApiBaseUrl()}/scans/delete-batch`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ scan_ids: scanIds, permanent }),
    });
    if (!res.ok) {
      throw new Error('No se pudieron eliminar los escaneos seleccionados');
    }
    return res.json();
  },

  async restoreScansBatch(
    token: string,
    scanIds: string[]
  ): Promise<{ restored_count: number }> {
    const res = await fetch(`${getApiBaseUrl()}/scans/restore-batch`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ scan_ids: scanIds }),
    });
    if (!res.ok) {
      throw new Error('No se pudieron restaurar los escaneos seleccionados');
    }
    return res.json();
  },

  /**
   * Construye el objeto source para <Image /> usando URL pre-firmada HMAC (?expires=...&sig=...)
   * y cabecera HTTP Authorization: Bearer <token>, sin exponer jamás el JWT en la URL.
   */
  getProtectedImageSource(
    relativeOrSignedPath: string,
    token: string
  ): { uri: string; headers: { Authorization: string } } {
    const hostRoot = getApiBaseUrl().replace(/\/api$/, '');
    const uri = relativeOrSignedPath.startsWith('http')
      ? relativeOrSignedPath
      : `${hostRoot}${relativeOrSignedPath}`;
    return {
      uri,
      headers: { Authorization: `Bearer ${token}` },
    };
  },
};
