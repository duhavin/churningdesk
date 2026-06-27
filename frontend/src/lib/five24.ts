import type { HeldCard } from "./api";

function addMonthsIso(value: string | null | undefined, months: number): string | null {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return null;
  const day = date.getDate();
  date.setMonth(date.getMonth() + months);
  if (date.getDate() !== day) date.setDate(0);
  return date.toISOString().slice(0, 10);
}

export function five24CardStatusLabel(card: HeldCard) {
  if (!card.reports_to_personal_credit) return "5/24: does not report";
  const dropDate = addMonthsIso(card.date_opened, 24);
  if (!dropDate) return "5/24: needs open date";
  const today = new Date().toISOString().slice(0, 10);
  return dropDate > today ? `5/24: counts until ${dropDate}` : `5/24: aged out ${dropDate}`;
}
