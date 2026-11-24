/** Store only an opaque task reference, scoped to the authenticated identity. */
export function taskStorageKey(tenantId: string, userId: string): string {
  return `assistant:last-task:${tenantId}:${userId}`;
}

/** Explicit links and explicit new-task mode take priority over navigation recovery. */
export function recoverTaskParams(
  current: URLSearchParams,
  remembered: string | null,
  latestId: string | null,
): URLSearchParams {
  if (current.get('run') || current.get('new') === '1') return current;
  if (remembered === 'new') return new URLSearchParams({ new: '1' });
  const id = remembered || latestId;
  return new URLSearchParams(id ? { run: id } : { new: '1' });
}
