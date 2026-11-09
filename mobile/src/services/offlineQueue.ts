import AsyncStorage from '@react-native-async-storage/async-storage';
import { apiClient } from '../api/client';
import { ScanItem } from '../types/models';

export interface OfflineScanItem {
  id: string;
  photoUri: string;
  siiTedXml?: string;
  precomputedBlurScore?: number;
  createdAt: string;
  status: 'PENDING' | 'SYNCING' | 'FAILED';
  errorMessage?: string;
}

const OFFLINE_QUEUE_KEY = '@smart_ocr_offline_queue_v1';

export const offlineQueue = {
  /**
   * Obtiene la lista de escaneos guardados localmente pendientes de sincronizar
   */
  async getPendingScans(): Promise<OfflineScanItem[]> {
    try {
      const raw = await AsyncStorage.getItem(OFFLINE_QUEUE_KEY);
      if (!raw) return [];
      return JSON.parse(raw);
    } catch {
      return [];
    }
  },

  /**
   * Agrega un nuevo escaneo a la cola local para subida en diferido
   */
  async enqueue(item: {
    photoUri: string;
    siiTedXml?: string;
    precomputedBlurScore?: number;
  }): Promise<OfflineScanItem> {
    const newItem: OfflineScanItem = {
      id: `offline_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      photoUri: item.photoUri,
      siiTedXml: item.siiTedXml,
      precomputedBlurScore: item.precomputedBlurScore,
      createdAt: new Date().toISOString(),
      status: 'PENDING',
    };

    const current = await this.getPendingScans();
    const updated = [newItem, ...current];
    await AsyncStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(updated));
    return newItem;
  },

  /**
   * Elimina un elemento de la cola local tras haberse sincronizado con éxito
   */
  async remove(id: string): Promise<void> {
    const current = await this.getPendingScans();
    const updated = current.filter((s) => s.id !== id);
    await AsyncStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(updated));
  },

  /**
   * Marca un elemento con error de sincronización
   */
  async markFailed(id: string, error: string): Promise<void> {
    const current = await this.getPendingScans();
    const updated = current.map((s) =>
      s.id === id ? { ...s, status: 'FAILED' as const, errorMessage: error } : s
    );
    await AsyncStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(updated));
  },

  /**
   * Sincroniza todos los escaneos pendientes contra el backend
   */
  async syncAll(
    token: string,
    onProgress?: (synced: number, total: number) => void
  ): Promise<{ synced: number; failed: number; uploadedScans: ScanItem[] }> {
    const items = await this.getPendingScans();
    if (items.length === 0) return { synced: 0, failed: 0, uploadedScans: [] };

    let synced = 0;
    let failed = 0;
    const uploadedScans: ScanItem[] = [];

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      try {
        const scan = await apiClient.uploadScanWebP(
          token,
          item.photoUri,
          true,
          item.siiTedXml,
          item.precomputedBlurScore
        );
        await this.remove(item.id);
        uploadedScans.push(scan);
        synced++;
      } catch (err: any) {
        await this.markFailed(item.id, err.message || 'Error de sincronización');
        failed++;
      }
      if (onProgress) {
        onProgress(synced + failed, items.length);
      }
    }

    return { synced, failed, uploadedScans };
  },
};
