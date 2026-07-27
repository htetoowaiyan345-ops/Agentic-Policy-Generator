<script lang="ts">
  import Header from '$lib/Header.svelte';
  import History from '$lib/History.svelte';
  import Upload from '$lib/Upload.svelte';
  import Process from '$lib/Process.svelte';
  import Review from '$lib/Review.svelte';
  import WorkflowTracker from '$lib/WorkflowTracker.svelte';
  import { appState, resetAppState, setFromHistory, setActiveRun } from '$lib/stores';
  import { onMount } from 'svelte';
  import type { StepId } from '$lib/types';

  let currentStep = $state<StepId>(1);
  let historyOpen = $state(false);

  function showStep(n: StepId): void {
    currentStep = n;
    if (typeof window !== 'undefined') {
      window.scrollTo({ top: 0, behavior: 'instant' });
    }
  }

  function goToStep2(): void {
    showStep(2);
  }

  function goBackFromStep3(): void {
    showStep(1);
  }

  function resetAll(): void {
    resetAppState();
    setFromHistory(false);
    currentStep = 1;
    historyOpen = false;
  }

  function addAnotherFile(): void {
    appState.update((s) => ({
      ...s,
      files: [],
      rejected: [],
      batchIndex: 0,
    }));
    currentStep = 1;
    historyOpen = false;
  }

  function toggleHistory(): void {
    historyOpen = !historyOpen;
  }

  // Stage 4.6 — Flow 2 notification "Open" navigation. Sets the
  // active run and jumps to Step 03 (Review).
  function onSelectRun(runId: string): void {
    setActiveRun(runId, null);
    historyOpen = false;
    showStep(3);
  }

  onMount(() => {
    showStep(1);
  });
</script>

<svelte:head>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous">
  <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,300..700;1,8..60,300..700&family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
</svelte:head>

<Header
  historyOpen={historyOpen}
  onToggleHistory={toggleHistory}
  onSelectRun={onSelectRun}
/>

<main class="p-8">
  <History open={historyOpen} onClose={() => (historyOpen = false)} />

  {#if currentStep === 1}
    <Upload onProceed={goToStep2} />
  {:else if currentStep === 2}
    <Process onReview={() => showStep(3)} />
  {:else if currentStep === 3}
    <div class="step-3-layout">
      <div class="step-3-main">
        <Review onBack={goBackFromStep3} onReset={resetAll} onAddAnother={addAnotherFile} />
      </div>
      <div class="step-3-side">
        <WorkflowTracker
          versions={$appState.versions}
          currentVersionNo={$appState.currentVersionNo}
          auditEvents={$appState.reviewAudit}
        />
      </div>
    </div>
  {/if}
</main>
