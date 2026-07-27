<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { currentUser, unreadSharedCount, setUnreadSharedCount } from './stores';
  import {
    getReviewerQueue,
    getMySharedProjects,
    type ReviewerQueueItem,
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
  // Two feed sections:
  //   - reviewerItems: in_review versions assigned to me (Stage 5/6 events)
  //   - sharedItems: projects I'm a member of (Flow 1 events)
  // The header `Notifications (N)` counter sums unread across both.
  let reviewerItems = $state<ReviewerQueueItem[]>([]);
  let sharedItems = $state<SharedProjectItem[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let pollHandle: ReturnType<typeof setInterval> | null = null;

  let reviewerUnread = $derived(
    reviewerItems.filter((d) => d.is_unread).length
  );
  let sharedUnread = $derived(
    sharedItems.filter((d) => d.is_unread).length
  );
  let unread = $derived(reviewerUnread + sharedUnread);

  async function refresh(): Promise<void> {
    if (!$currentUser) {
      reviewerItems = [];
      sharedItems = [];
      return;
    }
    try {
      const [rev, shr] = await Promise.all([
        getReviewerQueue(),
        getMySharedProjects()
      ]);
      reviewerItems = rev;
      sharedItems = shr;
      setUnreadSharedCount(rev.filter((d) => d.is_unread).length
        + shr.filter((d) => d.is_unread).length);
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
    // When the user changes (login/logout), restart polling
    if ($currentUser && !pollHandle) {
      loading = true;
      refresh();
      pollHandle = setInterval(refresh, 30_000);
    } else if (!$currentUser && pollHandle) {
      clearInterval(pollHandle);
      pollHandle = null;
      reviewerItems = [];
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
        <div class="nb-head mono-label">NOTIFICATIONS</div>
        {#if loading && reviewerItems.length === 0 && sharedItems.length === 0}
          <div class="nb-empty mono-underline">Loading…</div>
        {:else if reviewerItems.length === 0 && sharedItems.length === 0}
          <div class="nb-empty mono-underline">No notifications.</div>
        {:else}
          {#if reviewerItems.length > 0}
            <div class="nb-section-label mono-underline">Awaiting your review</div>
            <ul class="nb-list">
              {#each reviewerItems as it (it.run_id + ':' + it.version_no)}
                <li class="nb-row nb-row-review" data-unread={it.is_unread}>
                  <div class="nb-row-text">
                    <div class="nb-row-title">
                      {it.filename || it.run_id}
                      <span class="nb-vtag mono-underline">v{it.version_no}</span>
                    </div>
                    <div class="nb-row-sub mono-underline">
                      {#if it.assigned_reviewer_user_id}
                        assigned to <strong>{it.submitted_by}</strong>
                      {:else}
                        submitted by <strong>{it.submitted_by}</strong>
                      {/if}
                      · {timeAgo(it.submitted_at)}
                    </div>
                  </div>
                  <button
                    type="button"
                    class="nb-open-btn"
                    data-testid="notification-open-review"
                    onclick={() => onOpen(it.run_id)}
                  >Open</button>
                </li>
              {/each}
            </ul>
          {/if}

          {#if sharedItems.length > 0}
            <div class="nb-section-label mono-underline">Shared with you</div>
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
        {/if}
        {#if error}
          <div class="nb-error mono-underline">Error: {error}</div>
        {/if}
      </div>
    {/if}
  </span>
{/if}
