/**
 * timeAgo — formats an ISO timestamp (e.g. `2026-07-21T08:26:12Z`) into a
 * compact human-readable relative time string used by the Flow 2
 * notification dropdown ("3 m ago", "2 h ago", "yesterday", "2026-07-19").
 *
 * The exact strings come from common conventions:
 *   - "just now"   (< 30s)
 *   - "1 m ago", "3 m ago"…  (minutes, < 60)
 *   - "1 h ago", "2 h ago"…  (hours, < 24)
 *   - "yesterday"   (24–48 h)
 *   - "3 d ago"    (days, < 7)
 *   - "2026-07-19"  (older than a week, fall back to ISO date)
 *
 * Accepts `null | undefined | ''` → returns "" (no crash for Flow 2
 * unseen rows that haven't been opened yet).
 */

const _MS = {
  second: 1_000,
  minute: 60 * 1_000,
  hour:   60 * 60 * 1_000,
  day:    24 * 60 * 60 * 1_000,
};

function _safeDate(input: string | null | undefined): Date | null {
  if (!input) return null;
  const d = new Date(input);
  return isNaN(d.getTime()) ? null : d;
}

export function timeAgo(input: string | null | undefined): string {
  const d = _safeDate(input);
  if (!d) return "";
  const diff = Date.now() - d.getTime();
  if (diff < 0) return "just now";
  if (diff < _MS.minute / 2) return "just now";
  const minutes = Math.floor(diff / _MS.minute);
  if (minutes < 60) return minutes <= 1 ? "1 m ago" : `${minutes} m ago`;
  const hours = Math.floor(diff / _MS.hour);
  if (hours < 24) return hours <= 1 ? "1 h ago" : `${hours} h ago`;
  const days = Math.floor(diff / _MS.day);
  if (days < 2) return "yesterday";
  if (days < 7) return `${days} d ago`;
  // Fall back to ISO yyyy-mm-dd for older timestamps
  return d.toISOString().slice(0, 10);
}

export function dateOnly(input: string | null | undefined): string {
  const d = _safeDate(input);
  if (!d) return "";
  return d.toISOString().slice(0, 10);
}

export function isUnread(
  addedAt: string | null | undefined,
  lastSeenAt: string | null | undefined
): boolean {
  if (!lastSeenAt) return true;
  if (!addedAt) return false;
  return lastSeenAt < addedAt;
}
