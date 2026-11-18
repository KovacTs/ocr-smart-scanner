import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  Platform,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import { apiClient } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { offlineQueue, OfflineScanItem } from '../services/offlineQueue';
import { ScanItem } from '../types/models';

interface Props {
  onSelectScan: (scan: ScanItem) => void;
  onOpenCamera: () => void;
}

export const HistoryScreen: React.FC<Props> = ({
  onSelectScan,
  onOpenCamera,
}) => {
  const { token, user, logout } = useAuth();
  const [scans, setScans] = useState<ScanItem[]>([]);
  const [offlineItems, setOfflineItems] = useState<OfflineScanItem[]>([]);
  const [syncingOffline, setSyncingOffline] = useState<boolean>(false);
  const [search, setSearch] = useState<string>('');
  const [viewTrash, setViewTrash] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [exporting, setExporting] = useState<boolean>(false);
  const [deleting, setDeleting] = useState<boolean>(false);
  const [restoring, setRestoring] = useState<boolean>(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const fetchScans = useCallback(
    async (silent = false) => {
      if (!token) return;
      if (!silent) setLoading(true);
      try {
        const [list, pending] = await Promise.all([
          apiClient.listScans(token, {
            search: search.trim() || undefined,
            only_deleted: viewTrash,
          }),
          offlineQueue.getPendingScans(),
        ]);
        setScans(list);
        setOfflineItems(pending);
        setSelectedIds((prev) =>
          prev.filter((id) => list.some((s) => s.id === id))
        );
      } catch (err: any) {
        if (!silent) {
          Alert.alert('Error', err.message || 'No se pudo cargar el historial');
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [token, search, viewTrash]
  );

  useEffect(() => {
    fetchScans(false);
  }, [fetchScans]);

  // Auto-refresco cada 2.5s mientras existan documentos en cola ('PROCESSING')
  useEffect(() => {
    const hasProcessing = scans.some((s) => s.status === 'PROCESSING');
    if (!hasProcessing || !token) return;

    const interval = setInterval(() => {
      fetchScans(true);
    }, 2500);

    return () => clearInterval(interval);
  }, [scans, token, fetchScans]);

  const handleSyncOffline = async () => {
    if (!token || syncingOffline) return;
    setSyncingOffline(true);
    try {
      const res = await offlineQueue.syncAll(token);
      if (Platform.OS !== 'web') {
        Haptics.notificationAsync(
          Haptics.NotificationFeedbackType.Success
        ).catch(() => {});
      }
      Alert.alert(
        'Sincronización Completada',
        `Se subieron exitosamente ${res.synced} comprobante(s) al servidor.` +
          (res.failed > 0
            ? ` (${res.failed} fallaron y permanecen en cola).`
            : '')
      );
      await fetchScans();
    } catch (err: any) {
      Alert.alert(
        'Error al sincronizar',
        err.message || 'Verifica tu conexión a internet.'
      );
    } finally {
      setSyncingOffline(false);
    }
  };

  const toggleSelection = (id: string) => {
    if (Platform.OS !== 'web') {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    }
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const toggleSelectAll = () => {
    if (selectedIds.length === scans.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(scans.map((s) => s.id));
    }
  };

  const handleBatchExportExcel = async () => {
    if (!token) return;
    if (scans.length === 0) {
      Alert.alert('Sin datos', 'Aún no hay escaneos para exportar a Excel.');
      return;
    }
    setExporting(true);
    try {
      await apiClient.downloadAndShareExcel(
        token,
        selectedIds.length > 0 ? selectedIds : undefined
      );
    } catch (err: any) {
      Alert.alert('Error al exportar Excel', err.message);
    } finally {
      setExporting(false);
    }
  };

  const handleBatchRestore = async () => {
    if (!token || selectedIds.length === 0) return;
    setRestoring(true);
    try {
      await apiClient.restoreScansBatch(token, selectedIds);
      setSelectedIds([]);
      await fetchScans();
    } catch (err: any) {
      Alert.alert(
        'Error al restaurar',
        err.message || 'Intenta nuevamente'
      );
    } finally {
      setRestoring(false);
    }
  };

  const handleBatchDelete = () => {
    if (!token || selectedIds.length === 0) return;

    const count = selectedIds.length;
    const isPermanent = viewTrash;
    Alert.alert(
      isPermanent ? 'Eliminación definitiva' : 'Mover a la Papelera Tributaria',
      isPermanent
        ? `¿Deseas eliminar definitivamente ${count} comprobante(s) y destruir sus imágenes WebP? Esta acción es irreversible.`
        : `Se moverán ${count} comprobante(s) a la Papelera de Reciclaje conservando el respaldo tributario para recuperación.`,
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: isPermanent ? 'Purgar definitivamente' : 'Mover a Papelera',
          style: 'destructive',
          onPress: async () => {
            setDeleting(true);
            try {
              await apiClient.deleteScansBatch(token, selectedIds, isPermanent);
              setSelectedIds([]);
              await fetchScans();
            } catch (err: any) {
              Alert.alert(
                'Error al eliminar',
                err.message || 'Intenta nuevamente'
              );
            } finally {
              setDeleting(false);
            }
          },
        },
      ]
    );
  };

  const exportLabel =
    selectedIds.length > 0
      ? `Exportar Excel (${selectedIds.length})`
      : `Exportar Excel (${scans.length})`;

  return (
    <View style={styles.container}>
      {/* Encabezado Principal */}
      <View style={styles.header}>
        <View style={styles.headerTextCol}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {viewTrash ? 'Papelera Tributaria' : 'Historial de Escaneos'}
          </Text>
          <Text style={styles.headerSubtitle} numberOfLines={1}>
            {user?.full_name || user?.email || 'Documentos WebP + OCR'}
          </Text>
        </View>

        <TouchableOpacity style={styles.logoutBtn} onPress={logout}>
          <Text style={styles.logoutBtnText}>Salir</Text>
        </TouchableOpacity>
      </View>

      {/* Selector de Vista: Activos vs Papelera Tributaria (Soft-Delete) */}
      <View style={styles.modeTabsRow}>
        <TouchableOpacity
          style={[styles.modeTab, !viewTrash && styles.modeTabActive]}
          onPress={() => {
            setSelectedIds([]);
            setViewTrash(false);
          }}
        >
          <Text
            style={[
              styles.modeTabText,
              !viewTrash && styles.modeTabTextActive,
            ]}
          >
            Documentos Activos
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.modeTab, viewTrash && styles.modeTabTrashActive]}
          onPress={() => {
            setSelectedIds([]);
            setViewTrash(true);
          }}
        >
          <Text
            style={[
              styles.modeTabText,
              viewTrash && styles.modeTabTextActive,
            ]}
          >
            Papelera (Respaldo)
          </Text>
        </TouchableOpacity>
      </View>

      {/* Banner de Sincronización Offline (Fase 2) */}
      {offlineItems.length > 0 && !viewTrash && (
        <View style={styles.offlineBanner}>
          <View style={styles.offlineInfo}>
            <Text style={styles.offlineTitle}>⚡ Modo Offline Activo</Text>
            <Text style={styles.offlineSubtitle}>
              {offlineItems.length} escaneo(s) guardado(s) en tu teléfono
            </Text>
          </View>
          <TouchableOpacity
            style={styles.offlineSyncBtn}
            onPress={handleSyncOffline}
            disabled={syncingOffline}
          >
            {syncingOffline ? (
              <ActivityIndicator color="#FFFFFF" size="small" />
            ) : (
              <Text style={styles.offlineSyncBtnText}>Sincronizar</Text>
            )}
          </TouchableOpacity>
        </View>
      )}

      {/* Fila de Botones Principales (50% / 50% Ancho Completo) */}
      {!viewTrash && (
        <View style={styles.actionRow}>
          <TouchableOpacity
            style={styles.newScanButton}
            onPress={onOpenCamera}
            activeOpacity={0.85}
          >
            <Text style={styles.newScanButtonText} numberOfLines={1}>
              + Nuevo Escaneo
            </Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={[
              styles.excelButton,
              scans.length === 0 && styles.excelButtonDisabled,
            ]}
            onPress={handleBatchExportExcel}
            disabled={exporting || scans.length === 0}
            activeOpacity={0.85}
          >
            {exporting ? (
              <ActivityIndicator color="#FFFFFF" size="small" />
            ) : (
              <Text style={styles.excelButtonText} numberOfLines={1}>
                {exportLabel}
              </Text>
            )}
          </TouchableOpacity>
        </View>
      )}

      {/* Barra de Selección / Papelera / Eliminación Múltiple */}
      {scans.length > 0 && (
        <View style={styles.selectionToolbar}>
          <TouchableOpacity
            style={styles.selectAllBtn}
            onPress={toggleSelectAll}
          >
            <Text style={styles.selectAllText}>
              {selectedIds.length === scans.length
                ? 'Deseleccionar todos'
                : 'Seleccionar todos'}
            </Text>
          </TouchableOpacity>

          {selectedIds.length > 0 && viewTrash && (
            <TouchableOpacity
              style={styles.restoreBatchBtn}
              onPress={handleBatchRestore}
              disabled={restoring}
            >
              {restoring ? (
                <ActivityIndicator color="#FFFFFF" size="small" />
              ) : (
                <Text style={styles.restoreBatchText} numberOfLines={1}>
                  Restaurar ({selectedIds.length})
                </Text>
              )}
            </TouchableOpacity>
          )}

          {selectedIds.length > 0 && (
            <TouchableOpacity
              style={styles.deleteBatchBtn}
              onPress={handleBatchDelete}
              disabled={deleting}
            >
              {deleting ? (
                <ActivityIndicator color="#FFFFFF" size="small" />
              ) : (
                <Text style={styles.deleteBatchText} numberOfLines={1}>
                  {viewTrash
                    ? `Purgar (${selectedIds.length})`
                    : `Papelera (${selectedIds.length})`}
                </Text>
              )}
            </TouchableOpacity>
          )}
        </View>
      )}

      {/* Buscador de Ancho Completo */}
      <View style={styles.searchWrapper}>
        <TextInput
          style={styles.searchInput}
          placeholder="Buscar por folio, RUT o razón social..."
          placeholderTextColor="#64748B"
          value={search}
          onChangeText={setSearch}
          onSubmitEditing={() => fetchScans(false)}
          returnKeyType="search"
        />
      </View>

      {/* Lista de Documentos Escaneados */}
      {loading ? (
        <ActivityIndicator
          size="large"
          color="#38BDF8"
          style={{ marginTop: 48 }}
        />
      ) : (
        <FlatList
          data={scans}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
          ListEmptyComponent={
            <View style={styles.emptyBox}>
              <Text style={styles.emptyTitle}>
                {viewTrash
                  ? 'La papelera está vacía'
                  : 'Sin documentos registrados'}
              </Text>
              <Text style={styles.emptyText}>
                {viewTrash
                  ? 'Los comprobantes eliminados aparecerán aquí para proteger tu respaldo tributario antes de su eliminación definitiva.'
                  : 'Presiona el botón "+ Nuevo Escaneo" arriba para capturar tu primera factura o boleta con validación de enfoque.'}
              </Text>
            </View>
          }
          renderItem={({ item }) => {
            const isSelected = selectedIds.includes(item.id);
            const issuerName =
              item.current_normalized_data?.issuer?.legal_name ||
              'Emisor Desconocido';
            const issuerRut =
              item.current_normalized_data?.issuer?.tax_id || 'S/RUT';
            const thumbSource = token
              ? apiClient.getProtectedImageSource(
                  item.signed_thumbnail_url || item.thumbnail_url,
                  token
                )
              : undefined;
            const sizeKb = Math.round((item.file_size_bytes || 0) / 1024);

            return (
              <TouchableOpacity
                style={[styles.card, isSelected && styles.cardSelected]}
                onPress={() => onSelectScan(item)}
                onLongPress={() => toggleSelection(item.id)}
                activeOpacity={0.85}
              >
                {thumbSource && (
                  <Image source={thumbSource} style={styles.thumbnail} />
                )}

                <View style={styles.cardBody}>
                  <View style={styles.cardTopRow}>
                    <Text
                      style={[
                        styles.docBadge,
                        item.status === 'PROCESSING' && styles.processingBadge,
                      ]}
                      numberOfLines={1}
                    >
                      {item.status === 'PROCESSING'
                        ? '⏳ EN COLA ARQ'
                        : item.document_type.replace('_', ' ')}
                    </Text>
                    <Text style={styles.webpBadge}>WebP {sizeKb} KB</Text>
                  </View>

                  <Text style={styles.issuerTitle} numberOfLines={1}>
                    {item.status === 'PROCESSING'
                      ? 'Procesando extracción OCR...'
                      : issuerName}
                  </Text>
                  <Text style={styles.metaText} numberOfLines={1}>
                    Folio: {item.folio || 'S/N'} • RUT: {issuerRut}
                  </Text>
                  <Text style={styles.metaText} numberOfLines={1}>
                    Fecha: {item.issue_date || 'N/A'} • v
                    {item.versions?.length || 1}
                  </Text>
                </View>

                <View style={styles.amountCol}>
                  <Text style={styles.amountValue} numberOfLines={1}>
                    ${(item.total_amount || 0).toLocaleString('es-CL')}
                  </Text>
                  <TouchableOpacity
                    style={[
                      styles.checkCircle,
                      isSelected && styles.checkCircleActive,
                    ]}
                    onPress={() => toggleSelection(item.id)}
                  >
                    <Text style={styles.checkText}>
                      {isSelected ? '✓' : ''}
                    </Text>
                  </TouchableOpacity>
                </View>
              </TouchableOpacity>
            );
          }}
        />
      )}
    </View>
  );
};

const topPad =
  Platform.OS === 'android' ? (StatusBar.currentHeight || 24) + 14 : 52;

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0F172A',
    paddingHorizontal: 16,
    paddingTop: topPad,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  headerTextCol: {
    flex: 1,
    marginRight: 12,
  },
  headerTitle: {
    color: '#F8FAFC',
    fontSize: 22,
    fontWeight: '800',
  },
  headerSubtitle: {
    color: '#94A3B8',
    fontSize: 13,
    marginTop: 2,
  },
  logoutBtn: {
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 10,
  },
  logoutBtnText: {
    color: '#F87171',
    fontWeight: '700',
    fontSize: 13,
  },
  modeTabsRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 10,
  },
  modeTab: {
    flex: 1,
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155',
    paddingVertical: 9,
    borderRadius: 10,
    alignItems: 'center',
  },
  modeTabActive: {
    backgroundColor: '#0369A1',
    borderColor: '#38BDF8',
  },
  modeTabTrashActive: {
    backgroundColor: '#7C2D12',
    borderColor: '#FB923C',
  },
  modeTabText: {
    color: '#94A3B8',
    fontWeight: '700',
    fontSize: 12,
  },
  modeTabTextActive: {
    color: '#FFFFFF',
  },
  actionRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 10,
  },
  newScanButton: {
    flex: 1,
    backgroundColor: '#0284C7',
    paddingVertical: 13,
    paddingHorizontal: 10,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  newScanButtonText: {
    color: '#FFFFFF',
    fontWeight: '800',
    fontSize: 14,
  },
  excelButton: {
    flex: 1,
    backgroundColor: '#16A34A',
    paddingVertical: 13,
    paddingHorizontal: 10,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  excelButtonDisabled: {
    backgroundColor: '#334155',
  },
  excelButtonText: {
    color: '#FFFFFF',
    fontWeight: '800',
    fontSize: 14,
  },
  selectionToolbar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  selectAllBtn: {
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 10,
  },
  selectAllText: {
    color: '#38BDF8',
    fontSize: 12,
    fontWeight: '700',
  },
  restoreBatchBtn: {
    flex: 1,
    backgroundColor: '#0284C7',
    paddingVertical: 10,
    paddingHorizontal: 10,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  restoreBatchText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
  deleteBatchBtn: {
    flex: 1,
    backgroundColor: '#DC2626',
    paddingVertical: 10,
    paddingHorizontal: 10,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  deleteBatchText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
  searchWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 14,
  },
  searchInput: {
    flex: 1,
    backgroundColor: '#1E293B',
    color: '#F8FAFC',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 11,
    borderWidth: 1,
    borderColor: '#334155',
    fontSize: 14,
  },
  listContent: {
    paddingBottom: 36,
  },
  emptyBox: {
    backgroundColor: '#1E293B',
    borderRadius: 14,
    padding: 24,
    marginTop: 28,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#334155',
  },
  emptyTitle: {
    color: '#F8FAFC',
    fontSize: 16,
    fontWeight: '700',
    marginBottom: 8,
  },
  emptyText: {
    color: '#94A3B8',
    textAlign: 'center',
    lineHeight: 20,
    fontSize: 13,
  },
  card: {
    flexDirection: 'row',
    backgroundColor: '#1E293B',
    borderRadius: 14,
    padding: 12,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#334155',
    alignItems: 'center',
  },
  cardSelected: {
    borderColor: '#38BDF8',
    backgroundColor: '#172554',
  },
  thumbnail: {
    width: 56,
    height: 74,
    borderRadius: 8,
    backgroundColor: '#0F172A',
    marginRight: 12,
  },
  cardBody: {
    flex: 1,
    marginRight: 8,
  },
  cardTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 4,
  },
  docBadge: {
    color: '#38BDF8',
    fontSize: 10,
    fontWeight: '800',
    flexShrink: 1,
  },
  webpBadge: {
    color: '#4ADE80',
    fontSize: 10,
    fontWeight: '700',
  },
  issuerTitle: {
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: '700',
    marginBottom: 2,
  },
  metaText: {
    color: '#94A3B8',
    fontSize: 12,
    marginTop: 1,
  },
  amountCol: {
    alignItems: 'flex-end',
    justifyContent: 'space-between',
    height: 66,
    minWidth: 74,
  },
  amountValue: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: '800',
  },
  checkCircle: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: '#64748B',
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkCircleActive: {
    backgroundColor: '#0284C7',
    borderColor: '#38BDF8',
  },
  checkText: {
    color: '#FFF',
    fontSize: 13,
    fontWeight: '800',
  },
  offlineBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(2, 132, 199, 0.15)',
    borderWidth: 1,
    borderColor: '#0284C7',
    borderRadius: 12,
    padding: 12,
    marginBottom: 12,
  },
  offlineInfo: {
    flex: 1,
    marginRight: 10,
  },
  offlineTitle: {
    color: '#38BDF8',
    fontSize: 13,
    fontWeight: '700',
    marginBottom: 2,
  },
  offlineSubtitle: {
    color: '#94A3B8',
    fontSize: 11,
  },
  offlineSyncBtn: {
    backgroundColor: '#0284C7',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  offlineSyncBtnText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '700',
  },
  processingBadge: {
    backgroundColor: '#3B82F6',
  },
});
