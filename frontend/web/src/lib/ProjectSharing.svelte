<script lang="ts">
  import {
    listProjectMembers,
    addProjectMember,
    removeProjectMember,
    listShareableUsers,
    type AccessLevel,
    type ProjectMember,
    type ShareableUser
  } from './api';

  interface Props {
    runId: string;
    onClose: () => void;
  }
  let { runId, onClose }: Props = $props();

  let members = $state<ProjectMember[]>([]);
  let shareables = $state<ShareableUser[]>([]);
  let yourAccess = $state<AccessLevel | null>(null);
  let projectName = $state('');

  let search = $state('');
  let loading = $state(false);
  let saving = $state(false);
  let error = $state<string | null>(null);
  let dirty = $state(false);

  // local in-memory map: userId -> access_level edited in this session.
  // On Save, we POST each new/changed entry; we DELETE removed entries.
  let pending = $state<Map<number, AccessLevel>>(new Map());
  let pendingRemovals = $state<Set<number>>(new Set());

  // Stage 4.5 popup is shown ONLY to approvers. If the upstream caller
  // ever invokes it for a non-approver we surface a friendly disable.
  let isApprover = $derived(yourAccess === 'approver');

  // ─────────────────────────────────────────────────────────────────
  // Filtered candidate list (the search-first type-ahead)
  // ─────────────────────────────────────────────────────────────────

  let candidates = $derived.by(() => {
    const q = search.trim().toLowerCase();
    const alreadyIds = new Set(members.map((m) => m.user_id));
    const pendingIds = new Set(pendingRemovals);
    const editableIds = new Set([
      ...alreadyIds,
      ...pending.keys(),
    ]);
    return shareables.filter((u) => {
      if (alreadyIds.has(u.id) && !pendingRemovals.has(u.id)) return false;
      if (!q) return false;  // search-first: don't show anything until typed
      if (!u.username.toLowerCase().includes(q)) return false;
      // Even with a query, hide anyone already in the project AND not
      // marked for removal (we filter via Set above so this is fine).
      editableIds.add(u.id);
      return true;
    });
  });

  // Members that will appear in the list right now, after applying pending
  // adds/removes.
  let visibleMembers = $derived.by(() => {
    const result: ProjectMember[] = [];
    const existingById = new Map(members.map((m) => [m.user_id, m]));
    for (const m of members) {
      if (pendingRemovals.has(m.user_id)) continue;
      const lvl = pending.get(m.user_id) ?? m.access_level;
      result.push({ ...m, access_level: lvl });
    }
    for (const [uid, lvl] of pending.entries()) {
      if (existingById.has(uid) || pendingRemovals.has(uid)) continue;
      const u = shareables.find((s) => s.id === uid);
      result.push({
        run_id: runId,
        user_id: uid,
        username: u?.username ?? `user#${uid}`,
        access_level: lvl,
        added_by_user_id: -1,
        added_at: new Date().toISOString()
      });
    }
    return result.sort((a, b) => a.username.localeCompare(b.username));
  });

  async function refresh(): Promise<void> {
    if (!runId) return;
    loading = true;
    error = null;
    try {
      const [proj, users] = await Promise.all([
        listProjectMembers(runId),
        listShareableUsers()
      ]);
      members = proj.items;
      yourAccess = proj.your_access as AccessLevel | null;
      projectName = proj.project.filename ?? '';
      shareables = users;
      pending = new Map();
      pendingRemovals = new Set();
      dirty = false;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function markDirty(): void {
    dirty = true;
  }

  function onPickCandidate(u: ShareableUser, level: AccessLevel): void {
    pending.set(u.id, level);
    pending = new Map(pending);
    pendingRemovals.delete(u.id);
    pendingRemovals = new Set(pendingRemovals);
    search = '';
    markDirty();
  }

  function onChangeLevel(userId: number, level: AccessLevel): void {
    pending.set(userId, level);
    pending = new Map(pending);
    pendingRemovals.delete(userId);
    pendingRemovals = new Set(pendingRemovals);
    markDirty();
  }

  function onRemove(userId: number): void {
    // If this user was a fresh `pending` add (not yet on server), drop
    // from the pending map entirely; otherwise mark for removal.
    const existing = members.find((m) => m.user_id === userId);
    if (existing) {
      pendingRemovals.add(userId);
      pendingRemovals = new Set(pendingRemovals);
      pending.delete(userId);
      pending = new Map(pending);
    } else {
      pending.delete(userId);
      pending = new Map(pending);
    }
    markDirty();
  }

  function onCancel(): void {
    pending = new Map();
    pendingRemovals = new Set();
    dirty = false;
    onClose();
  }

  async function onSave(): Promise<void> {
    if (!runId) return;
    saving = true;
    error = null;
    try {
      // 1) Applies / changes
      for (const [uid, lvl] of pending.entries()) {
        await addProjectMember(runId, uid, lvl);
      }
      // 2) Removals
      for (const uid of pendingRemovals) {
        await removeProjectMember(runId, uid);
      }
      await refresh();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  $effect(() => {
    refresh();
  });

  function onBackdrop(e: MouseEvent): void {
    if (e.target === e.currentTarget) {
      onCancel();
    }
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape') onCancel();
  }
</script>

<svelte:window onkeydown={onKey} />

<div
  class="ps-backdrop"
  onclick={onBackdrop}
  onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') onBackdrop(e as unknown as MouseEvent); }}
  role="presentation"
  tabindex="-1"
>
  <div class="ps-modal" role="dialog" aria-modal="true" aria-label="Share project">
    <div class="ps-header">
      <span class="mono-label">SHARE PROJECT</span>
      {#if projectName}
        <span class="ps-project mono-underline">{projectName}</span>
      {/if}
      <button type="button" class="ps-close" onclick={onCancel} aria-label="Close">×</button>
    </div>

    {#if !isApprover}
      <div class="ps-readonly mono-underline">
        You need Approver access to change who is shared with this project.
      </div>
    {:else}
      <div class="ps-search-row">
        <input
          type="text"
          class="ps-search-input"
          placeholder="Search users by name…"
          bind:value={search}
          aria-label="Search users"
        />
      </div>

      {#if search.trim() && candidates.length > 0}
        <div class="ps-pick-list" role="listbox" aria-label="Pick a user">
          {#each candidates as u (u.id)}
            <div class="ps-pick">
              <span class="ps-pick-name">{u.username}</span>
              <div class="ps-pick-actions">
                <button type="button" class="ps-pick-level" onclick={() => onPickCandidate(u, 'viewer')}>Viewer</button>
                <button type="button" class="ps-pick-level" onclick={() => onPickCandidate(u, 'editor')}>Editor</button>
                <button type="button" class="ps-pick-level" onclick={() => onPickCandidate(u, 'approver')}>Approver</button>
              </div>
            </div>
          {/each}
        </div>
      {/if}

      <div class="ps-members mono-underline">
        Who has access ({visibleMembers.length})
      </div>
      {#if loading}
        <div class="ps-empty mono-underline">Loading…</div>
      {:else if visibleMembers.length === 0}
        <div class="ps-empty mono-underline">No other users have access yet.</div>
      {:else}
        <ul class="ps-list">
          {#each visibleMembers as m (m.user_id)}
            <li class="ps-row" data-uncommitted={pending.has(m.user_id) || pendingRemovals.has(m.user_id)}>
              <span class="ps-row-name">{m.username}</span>
              <select
                class="ps-row-level"
                value={m.access_level}
                onchange={(e) => onChangeLevel(m.user_id, ((e.target as HTMLSelectElement).value) as AccessLevel)}
                aria-label={`Access level for ${m.username}`}
              >
                <option value="viewer">Viewer</option>
                <option value="editor">Editor</option>
                <option value="approver">Approver</option>
              </select>
              <button
                type="button"
                class="ps-row-remove"
                onclick={() => onRemove(m.user_id)}
                aria-label={`Remove ${m.username}`}
                title="Remove from project"
              >×</button>
            </li>
          {/each}
        </ul>
      {/if}

      {#if error}
        <div class="ps-error mono-underline">Error: {error}</div>
      {/if}

      <div class="ps-footer">
        <button type="button" class="ps-cancel-btn" onclick={onCancel}>Cancel</button>
        <button
          type="button"
          class="ps-save-btn"
          onclick={onSave}
          disabled={!dirty || saving}
        >{saving ? 'Saving…' : 'Save'}</button>
      </div>
    {/if}
  </div>
</div>
