import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Image,
  Platform,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { apiClient } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { NormalizedDocumentData, ScanItem } from '../types/models';

interface Props {
  scan: ScanItem;
  onBack: () => void;
  onScanUpdated: (updated: ScanItem) => void;
}

export const ScanDetailScreen: React.FC<Props> = ({
  scan,
  onBack,
  onScanUpdated,
}) => {
  const { token } = useAuth();
  const [selectedVersionIdx, setSelectedVersionIdx] = useState<number>(0);
  const [zoomed, setZoomed] = useState<boolean>(false);
  const [saving, setSaving] = useState<boolean>(false);
  const [exporting, setExporting] = useState<boolean>(false);
  const [deleting, setDeleting] = useState<boolean>(false);

  const activeVersion = scan.versions?.[selectedVersionIdx];
  const initialNorm: NormalizedDocumentData =
    activeVersion?.normalized_data || scan.current_normalized_data;

  const [docType, setDocType] = useState(
    initialNorm.document_type || 'FACTURA_ELECTRONICA'
  );
  const [folio, setFolio] = useState(initialNorm.document_number || '');
  const [issueDate, setIssueDate] = useState(initialNorm.issue_date || '');
  const [issuerRut, setIssuerRut] = useState(initialNorm.issuer?.tax_id || '');
  const [issuerName, setIssuerName] = useState(
    initialNorm.issuer?.legal_name || ''
  );
  const [receiverRut, setReceiverRut] = useState(
    initialNorm.receiver?.tax_id || ''
  );
  const [netAmount, setNetAmount] = useState(
    String(initialNorm.amounts?.net_amount || 0)
  );
  const [taxAmount, setTaxAmount] = useState(
    String(initialNorm.amounts?.tax_amount || 0)
  );
  const [totalAmount, setTotalAmount] = useState(
    String(initialNorm.amounts?.total_amount || 0)
  );

  const loadVersionData = (idx: number) => {
    setSelectedVersionIdx(idx);
    const v = scan.versions[idx]?.normalized_data;
    if (!v) return;
    setDocType(v.document_type || 'FACTURA_ELECTRONICA');
    setFolio(v.document_number || '');
    setIssueDate(v.issue_date || '');
    setIssuerRut(v.issuer?.tax_id || '');
    setIssuerName(v.issuer?.legal_name || '');
    setReceiverRut(v.receiver?.tax_id || '');
    setNetAmount(String(v.amounts?.net_amount || 0));
    setTaxAmount(String(v.amounts?.tax_amount || 0));
    setTotalAmount(String(v.amounts?.total_amount || 0));
  };

  const handleSaveNewVersion = async () => {
    if (!token) return;
    setSaving(true);
    try {
      const payload: NormalizedDocumentData = {
        document_type: docType,
        document_number: folio,
        issue_date: issueDate,
        issuer: {
          tax_id: issuerRut,
          legal_name: issuerName,
          address: initialNorm.issuer?.address || '',
        },
        receiver: {
          tax_id: receiverRut,
          legal_name: initialNorm.receiver?.legal_name || '',
          address: '',
        },
        amounts: {
          net_amount: parseFloat(netAmount) || 0,
          tax_amount: parseFloat(taxAmount) || 0,
          exempt_amount: initialNorm.amounts?.exempt_amount || 0,
          total_amount: parseFloat(totalAmount) || 0,
          currency: initialNorm.amounts?.currency || 'CLP',
        },
        confidence_score: 1.0,
      };

      const updated = await apiClient.updateScanVersion(
        token,
        scan.id,
        payload
      );
      onScanUpdated(updated);
      setSelectedVersionIdx(0);
      Alert.alert(
        'Nueva versión guardada',
        `Se creó la Versión #${updated.versions[0]?.version_number} preservando el historial anterior.`
      );
    } catch (err: any) {
      Alert.alert('Error', err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleExportSingleExcel = async () => {
    if (!token) return;
    setExporting(true);
    try {
      await apiClient.downloadAndShareExcel(token, [scan.id]);
    } catch (err: any) {
      Alert.alert('Error exportando Excel', err.message);
    } finally {
      setExporting(false);
    }
  };

  const handleDeleteScan = () => {
    if (!token) return;
    Alert.alert(
      'Eliminar comprobante',
      '¿Estás seguro de que deseas eliminar este escaneo y su imagen original del historial?',
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Eliminar',
          style: 'destructive',
          onPress: async () => {
            setDeleting(true);
            try {
              await apiClient.deleteScan(token, scan.id);
              onBack();
            } catch (err: any) {
              Alert.alert('Error al eliminar', err.message);
            } finally {
              setDeleting(false);
            }
          },
        },
      ]
    );
  };

  const fullWebpUrl = token
    ? apiClient.getProtectedImageUrl(scan.image_url, token)
    : '';
  const origKb = Math.round((scan.original_size_bytes || 0) / 1024);
  const webpKb = Math.round((scan.file_size_bytes || 0) / 1024);
  const savedPct =
    origKb > 0 ? Math.max(0, Math.round((1 - webpKb / origKb) * 100)) : 90;

  return (
    <View style={styles.container}>
      {/* Barra Superior Responsiva */}
      <View style={styles.topHeader}>
        <TouchableOpacity onPress={onBack} style={styles.backBtn}>
          <Text style={styles.backText}>← Historial</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>
          Detalle del Comprobante
        </Text>
        <TouchableOpacity
          onPress={handleExportSingleExcel}
          style={styles.excelHeaderBtn}
          disabled={exporting}
        >
          <Text style={styles.excelHeaderText}>Excel</Text>
        </TouchableOpacity>
      </View>

      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        {/* Panel 1: Imagen Original en WebP con Zoom */}
        <View style={styles.imageSection}>
          <View style={styles.imageHeaderRow}>
            <Text style={styles.sectionLabel}>
              IMAGEN DE ORIGEN (WEBP OPTIMIZADO)
            </Text>
            <TouchableOpacity onPress={() => setZoomed(!zoomed)}>
              <Text style={styles.zoomToggle}>
                {zoomed ? 'Reducir Vista' : 'Ampliar Zoom HD'}
              </Text>
            </TouchableOpacity>
          </View>

          <Image
            source={{ uri: fullWebpUrl }}
            style={[styles.docImage, zoomed && styles.docImageZoomed]}
            resizeMode="contain"
          />

          <Text style={styles.compressionMeta}>
            Optimización WebP: {origKb} KB → {webpKb} KB (-{savedPct}%) • Score
            Enfoque: {scan.blur_score?.toFixed(0)}
          </Text>
        </View>

        {/* Selector de Versiones del Historial */}
        <View style={styles.versionSection}>
          <Text style={styles.sectionLabel}>
            HISTORIAL DE VERSIONES AUDITABLES ({scan.versions?.length || 1})
          </Text>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            style={styles.versionPillsRow}
          >
            {scan.versions?.map((v, idx) => (
              <TouchableOpacity
                key={v.id}
                style={[
                  styles.versionPill,
                  selectedVersionIdx === idx && styles.versionPillActive,
                ]}
                onPress={() => loadVersionData(idx)}
              >
                <Text style={styles.versionPillTitle}>
                  Versión #{v.version_number}
                </Text>
                <Text style={styles.versionPillSub}>{v.modified_by}</Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        </View>

        {/* Panel 2: Datos Normalizados y Filtrados */}
        <View style={styles.formSection}>
          <Text style={styles.sectionLabel}>
            DATOS NORMALIZADOS (RUIDO DESCARTADO)
          </Text>

          <View style={styles.row}>
            <View style={styles.col}>
              <Text style={styles.fieldLabel}>Tipo Documento</Text>
              <TextInput
                style={styles.input}
                value={docType}
                onChangeText={setDocType}
              />
            </View>
            <View style={styles.col}>
              <Text style={styles.fieldLabel}>Folio / N°</Text>
              <TextInput
                style={styles.input}
                value={folio}
                onChangeText={setFolio}
              />
            </View>
          </View>

          <View style={styles.row}>
            <View style={styles.col}>
              <Text style={styles.fieldLabel}>Fecha Emisión (ISO)</Text>
              <TextInput
                style={styles.input}
                value={issueDate}
                onChangeText={setIssueDate}
              />
            </View>
            <View style={styles.col}>
              <Text style={styles.fieldLabel}>RUT Emisor</Text>
              <TextInput
                style={styles.input}
                value={issuerRut}
                onChangeText={setIssuerRut}
              />
            </View>
          </View>

          <Text style={styles.fieldLabel}>Razón Social Emisor</Text>
          <TextInput
            style={styles.input}
            value={issuerName}
            onChangeText={setIssuerName}
          />

          <Text style={styles.fieldLabel}>RUT Receptor</Text>
          <TextInput
            style={styles.input}
            value={receiverRut}
            onChangeText={setReceiverRut}
          />

          <View style={styles.row}>
            <View style={styles.col}>
              <Text style={styles.fieldLabel}>Monto Neto ($)</Text>
              <TextInput
                style={styles.input}
                keyboardType="numeric"
                value={netAmount}
                onChangeText={setNetAmount}
              />
            </View>
            <View style={styles.col}>
              <Text style={styles.fieldLabel}>IVA 19% ($)</Text>
              <TextInput
                style={styles.input}
                keyboardType="numeric"
                value={taxAmount}
                onChangeText={setTaxAmount}
              />
            </View>
            <View style={styles.col}>
              <Text style={styles.fieldLabel}>Total ($)</Text>
              <TextInput
                style={styles.input}
                keyboardType="numeric"
                value={totalAmount}
                onChangeText={setTotalAmount}
              />
            </View>
          </View>

          <TouchableOpacity
            style={styles.saveVersionBtn}
            onPress={handleSaveNewVersion}
            disabled={saving}
          >
            {saving ? (
              <ActivityIndicator color="#FFF" />
            ) : (
              <Text style={styles.saveVersionText}>
                Guardar Corrección como Nueva Versión
              </Text>
            )}
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.fullExcelBtn}
            onPress={handleExportSingleExcel}
            disabled={exporting}
          >
            {exporting ? (
              <ActivityIndicator color="#FFF" />
            ) : (
              <Text style={styles.fullExcelText}>
                Exportar este Comprobante a Excel (.xlsx)
              </Text>
            )}
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.deleteBtn}
            onPress={handleDeleteScan}
            disabled={deleting}
          >
            {deleting ? (
              <ActivityIndicator color="#FFF" />
            ) : (
              <Text style={styles.deleteBtnText}>
                Eliminar este Comprobante
              </Text>
            )}
          </TouchableOpacity>
        </View>
      </ScrollView>
    </View>
  );
};

const topPad =
  Platform.OS === 'android' ? (StatusBar.currentHeight || 24) + 12 : 48;

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0F172A' },
  topHeader: {
    paddingTop: topPad,
    paddingHorizontal: 16,
    paddingBottom: 12,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderBottomWidth: 1,
    borderBottomColor: '#1E293B',
    gap: 8,
  },
  backBtn: {
    backgroundColor: '#1E293B',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#334155',
  },
  backText: { color: '#38BDF8', fontWeight: '700', fontSize: 13 },
  headerTitle: {
    flex: 1,
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: '800',
    textAlign: 'center',
  },
  excelHeaderBtn: {
    backgroundColor: '#16A34A',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
  },
  excelHeaderText: { color: '#FFF', fontWeight: '800', fontSize: 13 },
  scrollContent: { padding: 16, paddingBottom: 40 },
  imageSection: {
    backgroundColor: '#1E293B',
    borderRadius: 14,
    padding: 12,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: '#334155',
  },
  imageHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  sectionLabel: {
    color: '#38BDF8',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.6,
    flexShrink: 1,
  },
  zoomToggle: { color: '#FACC15', fontSize: 12, fontWeight: '700' },
  docImage: {
    width: '100%',
    height: 220,
    backgroundColor: '#0F172A',
    borderRadius: 10,
  },
  docImageZoomed: {
    height: 460,
  },
  compressionMeta: {
    color: '#94A3B8',
    fontSize: 11,
    marginTop: 8,
    textAlign: 'center',
  },
  versionSection: {
    marginBottom: 14,
  },
  versionPillsRow: {
    marginTop: 8,
  },
  versionPill: {
    backgroundColor: '#1E293B',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginRight: 10,
    borderWidth: 1,
    borderColor: '#334155',
  },
  versionPillActive: {
    borderColor: '#38BDF8',
    backgroundColor: '#0284C7',
  },
  versionPillTitle: { color: '#F8FAFC', fontWeight: '700', fontSize: 13 },
  versionPillSub: { color: '#CBD5E1', fontSize: 10, marginTop: 2 },
  formSection: {
    backgroundColor: '#1E293B',
    borderRadius: 14,
    padding: 14,
    borderWidth: 1,
    borderColor: '#334155',
  },
  row: { flexDirection: 'row', gap: 10 },
  col: { flex: 1 },
  fieldLabel: {
    color: '#94A3B8',
    fontSize: 12,
    fontWeight: '600',
    marginTop: 10,
    marginBottom: 4,
  },
  input: {
    backgroundColor: '#0F172A',
    color: '#F8FAFC',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    borderWidth: 1,
    borderColor: '#334155',
  },
  saveVersionBtn: {
    backgroundColor: '#0284C7',
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
    marginTop: 18,
  },
  saveVersionText: {
    color: '#FFF',
    fontWeight: '800',
    fontSize: 14,
  },
  fullExcelBtn: {
    backgroundColor: '#16A34A',
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
    marginTop: 10,
  },
  fullExcelText: {
    color: '#FFF',
    fontWeight: '800',
    fontSize: 14,
  },
  deleteBtn: {
    backgroundColor: '#DC2626',
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
    marginTop: 10,
  },
  deleteBtnText: {
    color: '#FFF',
    fontWeight: '800',
    fontSize: 14,
  },
});
