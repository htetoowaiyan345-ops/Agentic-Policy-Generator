export function escapeHtml(s: string): string {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[c] as string));
}

export function fmtMB(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
}
