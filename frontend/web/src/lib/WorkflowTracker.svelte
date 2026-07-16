<script lang="ts">
  import type { VersionEntry, AuditEntry, ReviewStatus } from './types';

  interface Props {
    versions: VersionEntry[];
    currentVersionNo: number | null;
    auditEvents: AuditEntry[];
    readonly?: boolean;
  }
  let { versions, currentVersionNo, auditEvents, readonly = false }: Props = $props();

  type NodeState = 'done' | 'active' | 'pending';
  interface Node {
    label: string;
    versionTag: string;
    state: NodeState;
    eventNote: string | null;
    last: boolean;
  }

  const NODES_TEMPL: Omit<Node, 'state' | 'eventNote'>[] = [
    { label: 'Created', versionTag: '', last: false },
    { label: 'Editing', versionTag: '', last: false },
    { label: 'Submitted for Review', versionTag: '', last: false },
    { label: 'In Review', versionTag: '', last: false },
    { label: 'Approved', versionTag: '', last: false },
    { label: 'Published', versionTag: '', last: false }
  ];

  function buildNodes(
    vs: VersionEntry[],
    cur: number | null,
    audits: AuditEntry[]
  ): Node[] {
    if (!vs || vs.length === 0) {
      return NODES_TEMPL.map((t) => ({ ...t, state: 'pending', eventNote: null }));
    }
    const cur_v = vs.find((v) => v.version_no === cur) || vs[vs.length - 1];
    const status: ReviewStatus = cur_v?.review_status || 'draft';
    const total_versions = vs.length;
    const total_comments = audits
      ? audits.filter((a) => a.event_type === 'comment_added').length
      : 0;
    const submit_audits = audits ? audits.filter((a) => a.event_type === 'submitted') : [];
    const most_recent_author =
      cur_v?.modified_by ||
      (vs[vs.length - 1]?.modified_by ?? 'unknown');

    const nodes: Node[] = [];
    // 1. Created - always done if any version exists
    nodes.push({
      label: 'Created',
      versionTag: vs[0] ? `(V${vs[0].version_no})` : '',
      state: 'done',
      eventNote: `by ${most_recent_author}`,
      last: false
    });
    // 2. Editing - active if status=draft (any unsaved/just-saved)
    nodes.push({
      label: 'Editing',
      versionTag: cur ? `(V${cur})` : '',
      state:
        status === 'draft'
          ? 'active'
          : status === 'in_review' ||
            status === 'approved' ||
            status === 'rejected' ||
            status === 'published'
          ? 'done'
          : 'pending',
      eventNote: `${total_versions} version${total_versions === 1 ? '' : 's'} so far`,
      last: false
    });
    // 3. Submitted for Review - done if submitted count > 0
    nodes.push({
      label: 'Submitted for Review',
      versionTag: '',
      state: submit_audits.length > 0 ? 'done' : 'pending',
      eventNote: null,
      last: false
    });
    // 4. In Review - active if status=in_review, done if approved/published, done if rejected
    nodes.push({
      label: 'In Review',
      versionTag: '',
      state:
        status === 'in_review'
          ? 'active'
          : status === 'approved' ||
            status === 'published' ||
            status === 'rejected'
          ? 'done'
          : 'pending',
      eventNote: null,
      last: false
    });
    // 5. Approved - done if approved or published
    nodes.push({
      label: 'Approved',
      versionTag: '',
      state:
        status === 'approved'
          ? 'active'
          : status === 'published'
          ? 'done'
          : 'pending',
      eventNote: null,
      last: false
    });
    // 6. Published - done only when published
    nodes.push({
      label: 'Published',
      versionTag: '',
      state: status === 'published' ? 'done' : 'pending',
      eventNote: null,
      last: true
    });
    return nodes;
  }

  let nodes = $derived(buildNodes(versions, currentVersionNo, auditEvents));

  function midNodeSummary(): string | null {
    if (!auditEvents) return null;
    const c = auditEvents.filter((a) => a.event_type === 'comment_added').length;
    if (c === 0) return null;
    return `${c} Comment${c === 1 ? '' : 's'}`;
  }

  let commentSummary = $derived(midNodeSummary());

  function fmtDate(iso: string | null | undefined): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return '';
    }
  }

  let modifiedAt = $derived(
    currentVersionNo && versions
      ? versions.find((v) => v.version_no === currentVersionNo)?.modified_at
      : null
  );
</script>

<aside class="wf-tracker" aria-label="Workflow progress tracker">
  <div class="wf-card">
    <div class="wf-header mono-label">WORKFLOW</div>
    {#if modifiedAt}
      <div class="wf-modified mono-tag">
        LAST SAVED {fmtDate(modifiedAt)}
      </div>
    {/if}
    <ol class="wf-list">
      {#each nodes as n, i (i)}
        <li class="wf-node" data-state={n.state}>
          <span class="wf-dot" aria-hidden="true">
            {n.state === 'done' ? '●' : n.state === 'active' ? '◉' : '○'}
          </span>
          <span class="wf-label">
            {n.label}{n.versionTag ? ' ' + n.versionTag : ''}
          </span>
          {#if n.eventNote}
            <div class="wf-event">{n.eventNote}</div>
          {/if}
        </li>
        {#if !n.last}
          <li class="wf-link" aria-hidden="true">
            <span class="wf-bar">│</span>
            {#if i === 1 && commentSummary}
              <span class="wf-mid">├─ {commentSummary}</span>
            {/if}
          </li>
        {/if}
      {/each}
    </ol>
    <div class="wf-legend">
      <span><span class="wf-dot">●</span> Done</span>
      <span><span class="wf-dot">◉</span> Current</span>
      <span><span class="wf-dot">○</span> Pending</span>
    </div>
    {#if readonly}
      <div class="wf-banner mono-tag">READ-ONLY VIEW</div>
    {/if}
  </div>
</aside>
