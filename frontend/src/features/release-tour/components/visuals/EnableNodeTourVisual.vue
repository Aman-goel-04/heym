<script setup lang="ts">
import { computed } from "vue";
import { Ban, CalendarClock, CircleCheck, Type } from "lucide-vue-next";

import { useCycleStep } from "@/features/release-tour/useCycleStep";

// Mock UI only. 0: the Cron node is switched off · 1: a request reaches the Input node ·
// 2: Enable Node runs · 3: the Cron node is back on and fires.
const step = useCycleStep(4, 1500);

const cronOn = computed<boolean>(() => step.value === 3);
const enableRunning = computed<boolean>(() => step.value === 2);
const inputActive = computed<boolean>(() => step.value === 1);

const caption = computed<string>(() => {
  if (step.value === 0) return "hourlyCheck is off";
  if (step.value === 1) return "Webhook request arrives";
  if (step.value === 2) return "Enable Node sets hourlyCheck to active";
  return "hourlyCheck runs again at 0 * * * *";
});
</script>

<template>
  <div class="flex h-full w-full flex-col justify-between rounded-lg bg-card p-3">
    <div class="flex items-center gap-2">
      <span
        class="flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] leading-none transition-colors duration-500"
        :class="
          inputActive
            ? 'border-primary/60 bg-primary/10 text-foreground'
            : 'border-border/60 bg-background text-muted-foreground'
        "
      >
        <Type class="h-3 w-3" />
        Input
      </span>
      <span class="h-px w-4 bg-border" />
      <span
        class="flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] leading-none transition-colors duration-500"
        :class="
          enableRunning
            ? 'border-node-enable bg-node-enable/10 text-node-enable'
            : 'border-border/60 bg-background text-muted-foreground'
        "
      >
        <CircleCheck class="h-3 w-3" />
        Enable Node
      </span>
    </div>

    <div class="flex items-center gap-2">
      <span
        class="flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] leading-none transition-all duration-500"
        :class="
          cronOn
            ? 'border-node-enable bg-node-enable/10 text-foreground'
            : 'border-dashed border-border/60 bg-background text-muted-foreground opacity-60'
        "
      >
        <CalendarClock class="h-3 w-3" />
        hourlyCheck
        <span
          class="ml-1 flex items-center gap-0.5 rounded px-1 py-0.5 text-[9px] leading-none transition-colors duration-500"
          :class="cronOn ? 'bg-node-enable/15 text-node-enable' : 'bg-muted text-muted-foreground'"
        >
          <Ban
            v-if="!cronOn"
            class="h-2 w-2"
          />
          {{ cronOn ? "on" : "off" }}
        </span>
      </span>
      <span class="h-px w-4 bg-border" />
      <span
        class="rounded-md border px-2 py-1 text-[10px] leading-none transition-opacity duration-500"
        :class="
          cronOn
            ? 'border-border/60 bg-background text-foreground opacity-100'
            : 'border-border/40 bg-background text-muted-foreground opacity-40'
        "
      >
        HTTP check
      </span>
    </div>

    <p class="truncate text-[10px] leading-none text-muted-foreground">
      {{ caption }}
    </p>
  </div>
</template>
