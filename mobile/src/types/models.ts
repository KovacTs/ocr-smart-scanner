export interface User {
  id: string;
  email: string;
  full_name: string;
  google_id?: string | null;
  avatar_url?: string | null;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  refresh_token?: string | null;
  token_type: string;
  user: User;
}

export interface PartyInfo {
  tax_id: string;
  legal_name: string;
  address: string;
}

export interface MonetaryAmounts {
  net_amount: number;
  tax_amount: number;
  exempt_amount: number;
  total_amount: number;
  currency: string;
}

export interface NormalizedDocumentData {
  document_type: string;
  document_number: string;
  issue_date: string;
  issuer: PartyInfo;
  receiver: PartyInfo;
  amounts: MonetaryAmounts;
  confidence_score: number;
}

export interface ScanVersion {
  id: string;
  scan_id: string;
  version_number: number;
  raw_text: string;
  normalized_data: NormalizedDocumentData;
  modified_by: string;
  created_at: string;
}

export interface ScanItem {
  id: string;
  user_id: string;
  document_type: string;
  folio: string | null;
  image_url: string;
  thumbnail_url: string;
  signed_image_url?: string | null;
  signed_thumbnail_url?: string | null;
  original_size_bytes: number;
  file_size_bytes: number;
  blur_score: number;
  current_raw_text: string;
  current_normalized_data: NormalizedDocumentData;
  issue_date: string | null;
  total_amount: number;
  status?: 'PROCESSING' | 'COMPLETED' | 'FAILED';
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
  versions: ScanVersion[];
}

export interface ScanStatusItem {
  id: string;
  status: 'PROCESSING' | 'COMPLETED' | 'FAILED';
  folio?: string;
  document_type?: string;
  total_amount?: number;
  issue_date?: string;
}

export interface FocusCheckResult {
  is_focused: boolean;
  document_detected: boolean;
  blur_score: number;
  threshold: number;
  roi_bbox: number[] | null;
  status_color: 'RED' | 'YELLOW' | 'GREEN';
  message: string;
}
