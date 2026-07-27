<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { currentUser, setUnreadSharedCount } from './stores';
  import {
    getMySharedProjects,
    markAllProjectsSeen,
    dismissAllNotifications,
    type SharedProjectItem
  } from './api';
  import { timeAgo } from './timeAgo';

  interface Props {
    /** Called when the user clicks "Open" on a notification. The parent
     *  navigates to step 3 and sets the active run (Review.svelte). */
    onSelectRun: (runId: string) => void;
  }
  let { onSelectRun }: Props = $props();

  let open = $state(false);
  let sharedItems = $state<SharedProjectItem[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let pollHandle: ReturnType<typeof setInterval> | null = null;

  let sharedUnread = $derived(
    sharedItems.filter((d) => d.is_unread).length
  );
  let unread = $derived(sharedUnread);

  async function refresh(): Promise<void> {
    if (!$currentUser) {
      sharedItems = [];
      setUnreadSharedCount(0);
      return;
    }
    try {
      const shr = await getMySharedProjects();
      sharedItems = shr;
      const n = shr.filter((d) => d.is_unread).length;
      setUnreadSharedCount(n);
      error = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function toggle(): void {
    open = !open;
    if (open) {
      loading = true;
      refresh();
    }
  }

  async function clearAll(): Promise<void> {
    try {
      // Per-user permanent dismissal — flags every notification as
      // dismissed in the DB so the bell stops surfacing them for THIS
      // user. Other members are unaffected.
      await dismissAllNotifications();
      setUnreadSharedCount(0);
      sharedItems = [];
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  function onDocClick(e: MouseEvent): void {
    if (!open) return;
    const target = e.target as HTMLElement | null;
    if (target && target.closest && target.closest('.nb-wrap')) return;
    open = false;
  }

  async function onOpen(runId: string): Promise<void> {
    open = false;
    onSelectRun(runId);
  }

  onMount(() => {
    if ($currentUser) {
      loading = true;
      refresh();
      pollHandle = setInterval(refresh, 30_000);
    }
    if (typeof document !== 'undefined') {
      document.addEventListener('click', onDocClick);
    }
  });

  $effect(() => {
    if ($currentUser && !pollHandle) {
      loading = true;
      refresh();
      pollHandle = setInterval(refresh, 30_000);
    } else if (!$currentUser && pollHandle) {
      clearInterval(pollHandle);
      pollHandle = null;
      sharedItems = [];
      setUnreadSharedCount(0);
    }
  });

  onDestroy(() => {
    if (pollHandle) clearInterval(pollHandle);
    if (typeof document !== 'undefined') {
      document.removeEventListener('click', onDocClick);
    }
  });
</script>

{#if $currentUser}
  <span class="nb-wrap">
    <button
      id="notification-bell"
      type="button"
      class="nb-trigger"
      data-testid="notification-bell"
      aria-haspopup="true"
      aria-expanded={open}
      onclick={toggle}
    >
      Notifications ({unread})
    </button>

    {#if open}
      <div class="nb-dropdown" role="menu" aria-label="Notifications">
        <div class="nb-head mono-label">
          <span>NOTIFICATIONS</span>
        </div>
        {#if loading && sharedItems.length === 0}
          <div class="nb-empty mono-underline">Loading…</div>
        {:else if sharedItems.length === 0}
          <div class="nb-empty mono-underline">No notifications.</div>
        {:else}
          <div class="nb-section-label mono-underline">
            <span>Shared with you</span>
            <button
              type="button"
              class="nb-clear-btn"
              data-testid="notification-clear-all"
              onclick={clearAll}
              title="Permanently remove all notifications for your account"
            >Clear all</button>
          </div>
          <ul class="nb-list">
            {#each sharedItems as it (it.run_id)}
              <li class="nb-row nb-row-share" data-unread={it.is_unread}>
                <div class="nb-row-text">
                  <div class="nb-row-title">
                    {it.filename || it.run_id}
                    <span class="nb-access-tag share-badge share-{it.your_access}">
                      {it.your_access}
                    </span>
                  </div>
                  <div class="nb-row-sub mono-underline">
                    {#if it.shared_by}
                      shared by <strong>{it.shared_by}</strong>
                    {:else}
                      your project
                    {/if}
                    · {timeAgo(it.added_at)}
                  </div>
                </div>
                <button
                  type="button"
                  class="nb-open-btn"
                  data-testid="notification-open-share"
                  onclick={() => onOpen(it.run_id)}
                >Open</button>
              </li>
            {/each}
          </ul>
        {/if}
        {#if error}
          <div class="nb-error mono-underline">Error: {error}</div>
        {/if}
      </div>
    {/if}
  </span>
{/if}