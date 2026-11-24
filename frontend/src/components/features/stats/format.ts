/** A share as a whole percentage; an em dash when there is nothing to divide. */
export const percent = (rate: number | null): string => (rate === null ? '—' : `${Math.round(rate * 100)}%`);
