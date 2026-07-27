<script lang="ts">
  import { currentUser, clearCurrentUser, resetAppState } from './stores';
  import { logout } from './api';
  import { clearToken } from './auth';
  import NotificationBell from './NotificationBell.svelte';

  interface Props {
    historyOpen: boolean;
    onToggleHistory: () => void;
    onSelectRun?: (runId: string) => void;
  }
  let { historyOpen, onToggleHistory, onSelectRun }: Props = $props();

  let user = $derived($currentUser);

  async function onLogout(): Promise<void> {
    await logout();
    clearToken();
    clearCurrentUser();
    resetAppState();
  }

  function handleSelectRun(runId: string): void {
    onSelectRun?.(runId);
  }
</script>

<header class="flex justify-between items-center px-8 py-4 border-b border-[#111111]">
  <a href="#" class="logo" id="logo-link">Agentic Policy Platform</a>
  <nav class="flex items-center gap-6">
    <a
      href="#"
      id="nav-history"
      class="font-mono text-[10px] font-medium tracking-[0.12em] uppercase text-[#111111] underline underline-offset-[3px]"
      onclick={(e) => {
        e.preventDefault();
        onToggleHistory();
      }}
    >Run History</a>
    {#if user}
      <NotificationBell onSelectRun={handleSelectRun} />
      <span
        class="font-mono text-[10px] font-medium tracking-[0.08em] uppercase text-[#555]"
        data-testid="current-user"
      >
        Logged in as <strong class="text-[#111111]">{user.username}</strong>{user.is_admin ? ' · admin' : ''}
      </span>
      <button
        id="logout-btn"
        type="button"
        class="font-mono text-[10px] font-medium tracking-[0.12em] uppercase text-[#111111] underline underline-offset-[3px] bg-transparent border-0 cursor-pointer p-0"
        onclick={onLogout}
      >Log out</button>
    {/if}
  </nav>
</header>
