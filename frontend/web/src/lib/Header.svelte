<script lang="ts">
  interface Props {
    historyOpen: boolean;
    onToggleHistory: () => void;
  }
  let { historyOpen, onToggleHistory }: Props = $props();
</script>

<header class="flex justify-between items-center px-8 py-4 border-b border-[#111111]">
  <a href="#" class="logo" id="logo-link">Agentic Policy Platform</a>
  <nav class="flex gap-6">
    <a
      href="#"
      id="nav-history"
      class="font-mono text-[10px] font-medium tracking-[0.12em] uppercase text-[#111111] underline underline-offset-[3px]"
      onclick={(e) => {
        e.preventDefault();
        onToggleHistory();
      }}
    >Run History</a>
  </nav>
</header>
