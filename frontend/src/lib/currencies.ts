import type { CatalogEntry, HeldCard, ProfileSummary } from "./api";

export const CORE_POINT_CURRENCIES = [
  "Amex Membership Rewards",
  "Chase Ultimate Rewards",
  "Capital One Miles",
  "Citi ThankYou Points",
  "Bilt Rewards",
  "Delta SkyMiles",
  "United MileagePlus",
  "Southwest Rapid Rewards",
  "American AAdvantage",
  "Alaska Mileage Plan",
  "JetBlue TrueBlue",
  "Marriott Bonvoy",
  "Hilton Honors",
  "World of Hyatt",
  "IHG One Rewards",
  "cash back",
];

const CANONICAL_CURRENCY_LABELS = new Map(
  CORE_POINT_CURRENCIES.map((currency) => [currency.toLowerCase(), currency]),
);

export function canonicalCurrencyLabel(value: unknown): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  const lower = text.toLowerCase();
  if (lower === "cash" || lower === "cashback" || lower === "cash back") return "cash back";
  if (lower.includes("thankyou") || lower.includes("thank you")) return "Citi ThankYou Points";
  if (lower.includes("ultimate rewards")) return "Chase Ultimate Rewards";
  if (lower.includes("membership rewards")) return "Amex Membership Rewards";
  if (lower.includes("capital one") && lower.includes("mile")) return "Capital One Miles";
  if (lower.includes("skymiles") || lower.includes("sky miles")) return "Delta SkyMiles";
  if (lower.includes("marriott")) return "Marriott Bonvoy";
  return CANONICAL_CURRENCY_LABELS.get(lower) ?? text;
}

export function buildCurrencyOptions({
  profile,
  cards,
  catalog,
  balances,
}: {
  profile?: ProfileSummary | null;
  cards?: HeldCard[];
  catalog?: CatalogEntry[];
  balances?: { currency: string; balance: string }[];
}): string[] {
  const byKey = new Map<string, string>();
  const add = (value: unknown) => {
    const label = canonicalCurrencyLabel(value);
    if (!label) return;
    const key = label.toLowerCase();
    if (!byKey.has(key)) byKey.set(key, label);
  };

  CORE_POINT_CURRENCIES.forEach(add);
  Object.keys(profile?.point_balances ?? {}).forEach(add);
  (cards ?? []).forEach((card) => add(card.bonus_currency));
  (catalog ?? []).forEach((entry) => add(entry.currency));
  (balances ?? []).forEach((row) => add(row.currency));

  return Array.from(byKey.values()).sort((a, b) => {
    if (a === "cash back") return 1;
    if (b === "cash back") return -1;
    return a.localeCompare(b);
  });
}
