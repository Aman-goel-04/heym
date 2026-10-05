import type {
  WorkIntegrationConfig,
  WorkIntegrationConfigUpdate,
  WorkKeyResponse,
} from "@/types/workIntegration";

import api from "@/services/api";

export async function getWorkIntegrationConfig(): Promise<WorkIntegrationConfig> {
  const { data } = await api.get<WorkIntegrationConfig>("/integrations/work/config");
  return data;
}

export async function saveWorkIntegrationConfig(
  update: WorkIntegrationConfigUpdate,
): Promise<WorkIntegrationConfig> {
  const { data } = await api.put<WorkIntegrationConfig>("/integrations/work/config", update);
  return data;
}

export async function rotateWorkIntegrationKey(): Promise<WorkKeyResponse> {
  const { data } = await api.post<WorkKeyResponse>("/integrations/work/key");
  return data;
}
