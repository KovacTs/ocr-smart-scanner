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
import { apiClient } from '../api/client';
import { useAuth } from '../context/AuthContext';
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
  const [search, setSearch] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [exporting, setExporting] = useState<boolean>(false);
  const [deleting, setDeleting] = useState<boolean>(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const fetchScans = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const list = await apiClient.listScans(token, {
        search: search.trim() || undefined,
      });
      setScans(list);
      setSelectedIds((prev) =>
        prev.filter((id) => list.some((s) => s.id === id))
      );
    } catch (err: any) {
      Alert.alert('Error', err.message || 'No se pudo cargar el historial');
    } finally {
      setLoading(false);
    }
  }, [token, search]);

  useEffect(() => {
    fetchScans();
  }, [fetchScans]);

  const toggleSelection = (id: string) => {
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

  const handleBatchDelete = () => {
    if (!token || selectedIds.length === 0) return;

    const count = selectedIds.length;
    Alert.alert(
      'Confirmar eliminación',
      count === 1
        ? '¿Estás seguro de que deseas eliminar este escaneo y su imagen original?'
        : `¿Estás seguro de que deseas eliminar los ${count} escaneos seleccionados? Esta acción no se puede deshacer.`,
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Eliminar',
          style: 'destructive',
          onPress: async () => {
            setDeleting(true);
            try {
              await apiClient.deleteScansBatch(token, selectedIds);
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
            Historial de Escaneos
          </Text>
          <Text style={styles.headerSubtitle} numberOfLines={1}>
            {user?.full_name || user?.email || 'Documentos WebP + OCR'}
          </Text>
        </View>

        <TouchableOpacity style={styles.logoutBtn} onPress={logout}>
          <Text style={styles.logoutBtnText}>Salir</Text>
        </TouchableOpacity>
      </View>

      {/* Fila de Botones Principales (50% / 50% Ancho Completo) */}
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

      {/* Barra de Eliminación Múltiple / Selección (Visible cuando hay elementos en la lista) */}
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
                  Eliminar ({selectedIds.length})
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
          onSubmitEditing={fetchScans}
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
              <Text style={styles.emptyTitle}>Sin documentos registrados</Text>
              <Text style={styles.emptyText}>
                Presiona el botón "+ Nuevo Escaneo" arriba para capturar tu
                primera factura o boleta con validación de enfoque.
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
            const thumbUri = token
              ? apiClient.getProtectedImageUrl(item.thumbnail_url, token)
              : '';
            const sizeKb = Math.round((item.file_size_bytes || 0) / 1024);

            return (
              <TouchableOpacity
                style={[styles.card, isSelected && styles.cardSelected]}
                onPress={() => onSelectScan(item)}
                onLongPress={() => toggleSelection(item.id)}
                activeOpacity={0.85}
              >
                <Image source={{ uri: thumbUri }} style={styles.thumbnail} />

                <View style={styles.cardBody}>
                  <View style={styles.cardTopRow}>
                    <Text style={styles.docBadge} numberOfLines={1}>
                      {item.document_type.replace('_', ' ')}
                    </Text>
                    <Text style={styles.webpBadge}>WebP {sizeKb} KB</Text>
                  </View>

                  <Text style={styles.issuerTitle} numberOfLines={1}>
                    {issuerName}
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
    marginBottom: 14,
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
    gap: 10,
    marginBottom: 10,
  },
  selectAllBtn: {
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 10,
  },
  selectAllText: {
    color: '#38BDF8',
    fontSize: 13,
    fontWeight: '700',
  },
  deleteBatchBtn: {
    flex: 1,
    backgroundColor: '#DC2626',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  deleteBatchText: {
    color: '#FFFFFF',
    fontSize: 13,
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
});
