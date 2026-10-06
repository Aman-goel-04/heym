<script setup lang="ts">
import { ref, watch } from "vue";

import axios from "axios";
import { Pause, Play, ShieldCheck } from "lucide-vue-next";

import type { WorkflowListItem } from "@/types/workflow";
import Button from "@/components/ui/Button.vue";
import { useToast } from "@/composables/useToast";
import { workflowApi } from "@/services/api";
import { useAuthStore } from "@/stores/auth";

const props = defineProps<{ workflow: WorkflowListItem; hasCron: boolean }>();
const emit = defineEmits<{ changed: [] }>();
const authStore = useAuthStore();
const { showToast } = useToast();
const busy = ref(false);
const paused = ref(props.workflow.trigger_status === "paused");

watch(() => props.workflow.trigger_status, (status) => {
  paused.value = status === "paused";
});

async function toggleTriggers(): Promise<void> {
  const resume = paused.value;
  const message = resume
    ? `Resume automatic triggers for "${props.workflow.name}"? Scheduled and event-driven runs can start again.`
    : `Pause automatic triggers for "${props.workflow.name}"? Existing runs can finish. Manual and API runs remain available.`;
  if (!confirm(message)) return;
  busy.value = true;
  try {
    if (resume) {
      await workflowApi.resumeTriggers(props.workflow.id);
    } else {
      await workflowApi.pauseTriggers(props.workflow.id);
    }
    paused.value = !resume;
    showToast(resume ? "Automatic triggers resumed." : "Automatic triggers paused. Existing runs can finish.");
    emit("changed");
  } catch (error: unknown) {
    showError(error);
  } finally {
    busy.value = false;
  }
}

function showError(error: unknown): void {
  const detail: unknown = axios.isAxiosError(error) ? error.response?.data?.detail : null;
  showToast(typeof detail === "string" ? detail : "Could not update this workflow.", "error");
}
</script>

<template>
  <section
    v-if="authStore.user?.is_admin"
    class="mb-5 rounded-xl border border-border bg-muted/25 p-3"
    data-testid="workflow-admin-controls"
  >
    <h3 class="flex items-center gap-2 text-xs font-semibold">
      <ShieldCheck class="h-4 w-4 text-primary" />
      Instance administration
    </h3>
    <p class="mt-2 break-all text-xs text-muted-foreground">
      {{ workflow.owner_email ? `Owner: ${workflow.owner_email}` : "Manage workflows across this instance." }}
    </p>
    <p
      v-if="hasCron"
      class="mt-1 text-xs text-muted-foreground"
    >
      Pause or resume automatic triggers. Existing runs can finish, and manual and API runs remain available.
    </p>
    <div
      v-if="hasCron"
      class="mt-3 flex flex-wrap gap-2"
    >
      <Button
        variant="outline"
        size="sm"
        :disabled="busy"
        :data-testid="paused ? 'workflow-admin-resume' : 'workflow-admin-pause'"
        @click="toggleTriggers"
      >
        <Play
          v-if="paused"
          class="h-3.5 w-3.5"
        />
        <Pause
          v-else
          class="h-3.5 w-3.5"
        />
        {{ paused ? "Resume triggers" : "Pause triggers" }}
      </Button>
    </div>
  </section>
</template>
