export interface WorkIntegrationConfig {
  enabled: boolean;
  work_url: string;
  key_set: boolean;
  key_rotated_at: string | null;
  last_seen_at: string | null;
  last_work_version: string;
  last_license_status: string;
}

export interface WorkIntegrationConfigUpdate {
  enabled?: boolean;
  work_url?: string;
}

export interface WorkKeyResponse {
  key: string;
  config: WorkIntegrationConfig;
}
