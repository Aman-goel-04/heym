<script setup lang="ts">
import Input from "@/components/ui/Input.vue";
import Label from "@/components/ui/Label.vue";
import { WAIT_MAX_DURATION_MS } from "@/lib/waitNode";
import { usePropertiesPanelContext } from "../usePropertiesPanelController";

const {
  selectedNode,
  handleDurationChange,
} = usePropertiesPanelContext();
</script>

<template>
  <template v-if="selectedNode">
    <div class="space-y-2">
      <Label>Duration (ms)</Label>
      <Input
        type="number"
        :model-value="selectedNode.data.duration || 1000"
        min="1"
        :max="WAIT_MAX_DURATION_MS"
        step="100"
        @update:model-value="handleDurationChange($event)"
      />
      <p class="text-xs text-muted-foreground">
        Wait time in milliseconds (1ms - 15 minutes)
      </p>
    </div>
  </template>
</template>
