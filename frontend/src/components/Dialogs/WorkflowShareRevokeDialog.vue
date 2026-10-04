<script setup lang="ts">
import { ref, watch } from "vue";

import Button from "@/components/ui/Button.vue";
import Dialog from "@/components/ui/Dialog.vue";

interface Props {
  open: boolean;
  subject: string;
  kind: "user" | "team";
  loading?: boolean;
}

const props = withDefaults(defineProps<Props>(), { loading: false });
const emit = defineEmits<{
  (e: "confirm", stopAlerts: boolean): void;
  (e: "cancel"): void;
}>();

const stopAlerts = ref(true);

watch(
  () => props.open,
  (open) => {
    if (open) stopAlerts.value = true;
  },
);
</script>

<template>
  <Dialog
    :open="open"
    title="Remove access"
    size="sm"
    @close="emit('cancel')"
  >
    <div
      class="space-y-4"
      data-testid="workflow-share-revoke-dialog"
    >
      <p class="text-sm text-muted-foreground">
        <strong class="font-medium text-foreground">{{ subject }}</strong>
        {{ kind === "team" ? "members" : "" }} will no longer be able to open or run this
        workflow.
      </p>
      <label class="flex items-start gap-2 text-sm">
        <input
          v-model="stopAlerts"
          type="checkbox"
          class="mt-0.5 rounded"
          data-testid="workflow-share-revoke-stop-alerts"
        >
        <span>
          Also stop their alerts on this workflow
          <span class="block text-xs text-muted-foreground">
            Uncheck to keep those alerts reporting this workflow's run metrics.
          </span>
        </span>
      </label>
      <div class="flex justify-end gap-2">
        <Button
          variant="outline"
          @click="emit('cancel')"
        >
          Cancel
        </Button>
        <Button
          variant="destructive"
          :loading="loading"
          data-testid="workflow-share-revoke-confirm"
          @click="emit('confirm', stopAlerts)"
        >
          Remove
        </Button>
      </div>
    </div>
  </Dialog>
</template>
