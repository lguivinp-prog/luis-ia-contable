// Replica en el cliente el algoritmo de digito verificador (modulo 11) del RUC
// peruano, solo para dar retroalimentacion inmediata. El backend siempre
// revalida (ver backend/app/services/ruc_validator.py) y es la fuente de verdad.

const VALID_RUC_PREFIXES = new Set(["10", "15", "16", "17", "20"]);
const CHECK_DIGIT_FACTORS = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];

function computeCheckDigit(firstTenDigits: string): number {
  const total = firstTenDigits
    .split("")
    .reduce((sum, digit, index) => sum + Number(digit) * CHECK_DIGIT_FACTORS[index], 0);
  const remainder = total % 11;
  const result = 11 - remainder;
  if (result === 10) return 0;
  if (result === 11) return 1;
  return result;
}

export function isValidRuc(ruc: string): boolean {
  if (!/^\d{11}$/.test(ruc)) return false;
  if (!VALID_RUC_PREFIXES.has(ruc.slice(0, 2))) return false;
  return computeCheckDigit(ruc.slice(0, 10)) === Number(ruc[10]);
}
