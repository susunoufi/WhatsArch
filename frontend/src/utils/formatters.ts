export function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function formatCost(cost: number): string {
  if (cost === 0) return '';
  if (cost < 0.01) return cost.toFixed(4);
  if (cost < 1) return cost.toFixed(2);
  return cost.toFixed(2);
}

export function formatEta(seconds: number): string {
  if (seconds > 3600) return `${Math.round(seconds / 3600)} hours`;
  if (seconds > 60) return `${Math.round(seconds / 60)} min`;
  return `${seconds}s`;
}

export function formatNumber(n: number): string {
  return n.toLocaleString();
}

export function senderColor(sender: string): string {
  const colors = [
    '#6366F1', '#0D9488', '#F59E0B', '#EF4444', '#8B5CF6',
    '#EC4899', '#14B8A6', '#F97316', '#06B6D4', '#84CC16',
  ];
  let hash = 0;
  for (let i = 0; i < sender.length; i++) {
    hash = sender.charCodeAt(i) + ((hash << 5) - hash);
  }
  return colors[Math.abs(hash) % colors.length];
}
