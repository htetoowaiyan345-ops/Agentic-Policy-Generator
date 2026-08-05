<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { currentUser, setUnreadSharedCount } from './stores';
  import {
    getMySharedProjects,
    getMySentShares,
    dismissAllNotifications,
    dismissAllSentShares,
    type SharedProjectItem,
    type SentShareItem
  } from './api';
  import { timeAgo } from './timeAgo';

  interface Props {
    /** Called when the user clicks "Open" on a notification. The parent
     *  navigates to step 3 and sets the active run (Review.svelte). */
    onSelectRun: (runId: string) => void;
  }
  let { onSelectRun }: Props = $props();

  // Combined list of incoming shares (someone shared with you) and
  // outgoing shares (you shared with someone). Each item carries a
  // `direction: 'in' | 'out'` discriminator so the row template can
  // render the right icon, color, and label without splitting into two
  // sections.
  type CombinedItem =
    | (SharedProjectItem & { direction: 'in' })
    | (SentShareItem & { direction: 'out' });

  let open = $state(false);
  let combinedItems = $state<CombinedItem[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let pollHandle: ReturnType<typeof setInterval> | null = null;

  let unread = $derived(
    combinedItems.filter((d) => d.is_unread).length
  );

  async function refresh(): Promise<void> {
    if (!$currentUser) {
      combinedItems = [];
      setUnreadSharedCount(0);
      return;
    }
    try {
      // Fetch both feeds in parallel — incoming (someone shared with
      // me) and outgoing (I shared with someone). The bell renders
      // them as one combined list with a per-row direction label.
      const [incoming, outgoing] = await Promise.all([
        getMySharedProjects(),
        getMySentShares().catch(() => [] as SentShareItem[])
      ]);
      const combined: CombinedItem[] = [
        ...incoming.map((it) => ({ ...it, direction: 'in' as const })),
        ...outgoing.map((it) => ({ ...it, direction: 'out' as const }))
      ];
      // Sort by timestamp desc (use added_at for incoming, created_at
      // for outgoing). Items missing a timestamp sort to the end.
      combined.sort((a, b) => {
        const ta = (a.direction === 'in' ? a.added_at : a.created_at) || '';
        const tb = (b.direction === 'in' ? b.added_at : b.created_at) || '';
        return tb.localeCompare(ta);
      });
      combinedItems = combined;
      setUnreadSharedCount(combined.filter((d) => d.is_unread).length);
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
      // Per-user permanent dismissal — flags both incoming and
      // outgoing notifications as dismissed in the DB so the bell
      // stops surfacing them for THIS user. Other members are
      // unaffected.
      await Promise.all([
        dismissAllNotifications(),
        dismissAllSentShares().catch(() => ({ ok: true, dismissed: 0 }))
      ]);
      setUnreadSharedCount(0);
      combinedItems = [];
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
      combinedItems = [];
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
        {#if loading && combinedItems.length === 0}
          <div class="nb-empty mono-underline">Loading…</div>
        {:else if combinedItems.length === 0}
          <div class="nb-empty mono-underline">No notifications.</div>
        {:else}
          <div class="nb-section-label mono-underline">
            <span>Shares</span>
            <button
              type="button"
              class="nb-clear-btn"
              data-testid="notification-clear-all"
              onclick={clearAll}
              title="Permanently remove all notifications for your account"
            >Clear all</button>
          </div>
          <ul class="nb-list">
            {#each combinedItems as it (it.direction + ':' + (it.direction === 'out' ? it.id : it.run_id))}
              <li
                class="nb-row nb-row-share {it.direction === 'out' ? 'nb-row-share-out' : 'nb-row-share-in'}"
                data-unread={it.is_unread}
                data-direction={it.direction}
              >
                <div class="nb-row-text">
                  <div class="nb-row-title">
                    {it.filename || it.run_id}
                    <span class="nb-row-direction nb-direction-{it.direction}">
                      {it.direction === 'out' ? '↑ share' : '↓ shared'}
                    </span>
                    {#if it.direction === 'in'}
                      <span class="nb-access-tag share-badge share-{it.your_access}">
                        {it.your_access}
                      </span>
                    {/if}
                  </div>
                  <div class="nb-row-sub mono-underline">
                    {#if it.direction === 'in'}
                      {#if it.shared_by}
                        shared by <strong>{it.shared_by}</strong>
                      {:else}
                        your project
                      {/if}
                      · {timeAgo(it.added_at)}
                    {:else}
                      you shared with <strong>{it.recipient_username}</strong>
                      · {timeAgo(it.created_at)}
                    {/if}
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