import * as ImageManipulator from 'expo-image-manipulator';
import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import {
  AuthResponse,
  FocusCheckResult,
  NormalizedDocumentData,
  ScanItem,
} from '../types/models';

export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL || 'http://192.168.1.168:8000/api';

/**
 * Comprime la foto en el dispositivo antes de subirla:
 * 1. Normaliza resolución (máximo 2400px de ancho).
 * 2. Codifica a formato WebP con calidad 82% (~90% reducción de peso).
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
  async register(
    email: string,
    password: string,
    fullName: string
  ): Promise<AuthResponse> {
    const res = await fetch(`${API_BASE_URL}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, full_name: fullName }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Error al registrar usuario');
    }
    return res.json();
  },

  async login(email: string, password: string): Promise<AuthResponse> {
    const res = await fetch(`${API_BASE_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Credenciales inválidas');
    }
    return res.json();
  },

  async evaluateFrameFocus(
    token: string,
    frameUri: string
  ): Promise<FocusCheckResult> {
    const formData = new FormData();
    const frameFile = new File(frameUri);
    formData.append('file', frameFile);

    const res = await fetch(`${API_BASE_URL}/scans/evaluate-focus`, {
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
    forceCapture = false
  ): Promise<ScanItem> {
    const compressed = await compressToWebPClientSide(rawPhotoUri);
    const formData = new FormData();
    const webpFile = new File(compressed.uri);
    formData.append('file', webpFile);
    formData.append('force_capture', String(forceCapture));

    const res = await fetch(`${API_BASE_URL}/scans`, {
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

  async listScans(
    token: string,
    params?: { search?: string; document_type?: string }
  ): Promise<ScanItem[]> {
    const query = new URLSearchParams();
    if (params?.search) query.set('search', params.search);
    if (params?.document_type) query.set('document_type', params.document_type);
    const qs = query.toString() ? `?${query.toString()}` : '';

    const res = await fetch(`${API_BASE_URL}/scans${qs}`, {
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
    const res = await fetch(`${API_BASE_URL}/scans/${scanId}`, {
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
    const res = await fetch(`${API_BASE_URL}/scans/export-batch`, {
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

  async deleteScan(token: string, scanId: string): Promise<void> {
    const res = await fetch(`${API_BASE_URL}/scans/${scanId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      throw new Error('No se pudo eliminar el escaneo');
    }
  },

  async deleteScansBatch(
    token: string,
    scanIds: string[]
  ): Promise<{ deleted_count: number }> {
    const res = await fetch(`${API_BASE_URL}/scans/delete-batch`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ scan_ids: scanIds }),
    });
    if (!res.ok) {
      throw new Error('No se pudieron eliminar los escaneos seleccionados');
    }
    return res.json();
  },

  getProtectedImageUrl(relativePath: string, token: string): string {
    const hostRoot = API_BASE_URL.replace(/\/api$/, '');
    return `${hostRoot}${relativePath}?token=${encodeURIComponent(token)}`;
  },
};
