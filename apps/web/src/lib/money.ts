/** Parse decimal text directly to integer fen; avoid floating-point multiplication. */
export function yuanToFen(input: string): number {
  const normalized = input.trim();
  if (!/^(0|[1-9]\d*)(?:\.\d{1,2})?$/.test(normalized)) {
    throw new Error('金额须为正数，最多保留两位小数');
  }
  const [whole = '0', fraction = ''] = normalized.split('.');
  const amount = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  if (amount < 1n || amount > 100_000_000n) throw new Error('金额须介于0.01元和1,000,000元之间');
  return Number(amount);
}

export function formatMoney(fen: number): string {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(fen / 100);
}
