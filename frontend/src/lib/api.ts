// Thin typed client over the FastAPI backend. All paths are relative; the Vite
// dev server proxies /api → :8000, and in production FastAPI serves both.

async function req<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// --- Types (loose) ---------------------------------------------------------
export interface Health {
  status: string;
  llm_available: boolean;
  crypto_available: boolean;
}
export interface RunStatus {
  llm_available: boolean;
  model: string | null;
  search_model?: string | null;
  web_search_enabled?: boolean;
  crypto_available: boolean;
}
export interface RefreshJob {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  processed: number;
  total: number;
  skipped: number;
  current_product: string | null;
  committed: number;
  proposed: number;
  errors: any[];
  warnings: any[];
  phase: string;
  result: any;
}
export interface ManualTargetedOffer {
  id: number;
  user: string;
  issuer: string;
  product_name: string;
  product_id: number | null;
  offer_points: number | null;
  offer_cash: number | null;
  expires_at: string | null;
  notes: string | null;
}
export interface Eligibility {
  eligible: boolean;
  block_type: string;
  reasons: string[];
  earliest_eligible_date: string | null;
}
export interface CatalogEntry {
  id: number;
  issuer: string;
  product_name: string;
  product_family: string | null;
  ownership: string;
  account_type: string;
  currency: string | null;
  reports_to_personal_credit: boolean;
  annual_fee: number | null;
  current_offer_points: number | null;
  current_offer_override: number | null;
  current_offer_effective: number | null;
  current_offer_cash: number | null;
  current_offer_min_spend: number | null;
  current_offer_window_months: number | null;
  peak_offer_points: number | null;
  peak_offer_effective: number | null;
  peak_offer_min_spend: number | null;
  targeted_peak_offer_points: number | null;
  targeted_peak_offer_cash: number | null;
  targeted_peak_offer_source: string | null;
  targeted_peak_offer_date: string | null;
  peak_offer_date: string | null;
  referral_bonus_points: number | null;
  referral_bonus_override: number | null;
  referral_bonus_effective: number | null;
  referral_bonus_cash: number | null;
  first_year_credit_value: number | null;
  earn_multipliers: Record<string, any> | null;
  best_category_uses: Record<string, any> | null;
  card_benefits: any[] | null;
  downgrade_paths: any[] | null;
  eligibility_tags: string[] | null;
  tag: string | null;
  added_by: string;
  discovery_reviewed: boolean;
  source_url: string | null;
  last_verified: string | null;
  eligibility: Eligibility;
  peak_score: number;
  offer_value: number;
  effective_points: number;
  targeted_beats_public: boolean;
  my_targeted_offer_points: number | null;
  manual_targeted_offer: ManualTargetedOffer | null;
  needs_data: boolean;
  value_known: boolean;
  peak_is_targeted: boolean;
  status: string;
  rank: number | null;
}
export interface HeldCard {
  id: number;
  user: string;
  product_id: number | null;
  issuer: string;
  product_name: string;
  last4: string | null;
  date_opened: string;
  ownership: string;
  account_type: string;
  reports_to_personal_credit: boolean;
  credit_limit: number | null;
  annual_fee: number | null;
  renewal_date: string | null;
  next_review_date: string | null;
  welcome_bonus_earned: boolean;
  bonus_points_earned: number | null;
  bonus_currency: string | null;
  bonus_earned_date: string | null;
  min_spend_requirement: number | null;
  min_spend_deadline: string | null;
  min_spend_progress: number | null;
  min_spend_completed: boolean;
  bonus_eligible_again: boolean;
  eligible_again_date: string | null;
  my_targeted_offer_points: number | null;
  my_targeted_offer_notes: string | null;
  status: string;
  notes: string | null;
}
export interface Five24 {
  count: number;
  under_524: boolean;
  earliest_drop_date: string | null;
  contributing?: { issuer: string; product_name: string; date_opened: string }[];
}

export const api = {
  health: () => req<Health>("/api/health"),
  users: () => req<string[]>("/api/users"),
  runStatus: () => req<RunStatus>("/api/run/status"),
  refreshStatus: () => req<RefreshJob>("/api/run/refresh/status"),

  // Held cards
  cards: (user: string) => req<HeldCard[]>(`/api/cards?user=${encodeURIComponent(user)}`),
  createCard: (data: any) => req<HeldCard>("/api/cards", { method: "POST", body: JSON.stringify(data) }),
  updateCard: (id: number, data: any) =>
    req<HeldCard>(`/api/cards/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteCard: (id: number) => req(`/api/cards/${id}`, { method: "DELETE" }),

  // Dashboard
  dashboard: (user: string) => req<any>(`/api/dashboard?user=${encodeURIComponent(user)}`),

  // Catalog
  catalog: (user: string) => req<CatalogEntry[]>(`/api/catalog?user=${encodeURIComponent(user)}`),
  createProduct: (data: any) => req<any>("/api/catalog", { method: "POST", body: JSON.stringify(data) }),
  updateProduct: (id: number, data: any) =>
    req<any>(`/api/catalog/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteProduct: (id: number) => req(`/api/catalog/${id}`, { method: "DELETE" }),
  targetedOffer: (productId: number, user: string) =>
    req<ManualTargetedOffer | null>(`/api/catalog/${productId}/targeted-offer?user=${encodeURIComponent(user)}`),
  saveTargetedOffer: (productId: number, user: string, data: any) =>
    req<ManualTargetedOffer>(`/api/catalog/${productId}/targeted-offer?user=${encodeURIComponent(user)}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteTargetedOffer: (productId: number, user: string) =>
    req(`/api/catalog/${productId}/targeted-offer?user=${encodeURIComponent(user)}`, { method: "DELETE" }),

  // Valuations
  valuations: () => req<any[]>("/api/valuations"),
  upsertValuation: (data: any) => req<any>("/api/valuations", { method: "PUT", body: JSON.stringify(data) }),

  // Watchlist / blacklist
  watchlist: () => req<any[]>("/api/watchlist"),
  addWatchlist: (data: any) => req<any>("/api/watchlist", { method: "POST", body: JSON.stringify(data) }),
  removeWatchlist: (id: number) => req(`/api/watchlist/${id}`, { method: "DELETE" }),
  blacklist: () => req<any[]>("/api/blacklist"),
  addBlacklist: (data: any) => req<any>("/api/blacklist", { method: "POST", body: JSON.stringify(data) }),
  removeBlacklist: (id: number) => req(`/api/blacklist/${id}`, { method: "DELETE" }),

  // Profiles
  profiles: () => req<any[]>("/api/profiles"),
  profile: (user: string) => req<any>(`/api/profiles/${encodeURIComponent(user)}`),
  upsertProfile: (user: string, data: any) =>
    req<any>(`/api/profiles/${encodeURIComponent(user)}`, { method: "PUT", body: JSON.stringify(data) }),

  // Pipeline
  pipeline: (user: string) => req<any>(`/api/pipeline?user=${encodeURIComponent(user)}`),

  // Household (combined two-user plan + referral synergy)
  household: () => req<any>("/api/household"),

  // Ingestion admin
  proposedChanges: (status = "pending") => req<any[]>(`/api/proposed-changes?status=${status}`),
  decideChange: (id: number, status: string) =>
    req<any>(`/api/proposed-changes/${id}/decision`, { method: "POST", body: JSON.stringify({ status }) }),
  discovered: () => req<any[]>("/api/discovered"),
  reviewDiscovered: (id: number) => req<any>(`/api/discovered/${id}/review`, { method: "POST" }),
  sources: () => req<any>("/api/sources"),
  createSource: (data: any) => req<any>("/api/sources", { method: "POST", body: JSON.stringify(data) }),
  updateSource: (id: number, data: any) =>
    req<any>(`/api/sources/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteSource: (id: number) => req<any>(`/api/sources/${id}`, { method: "DELETE" }),

  // Run menu
  runDiscover: (issuers?: string[]) =>
    req<any>("/api/run/discover", { method: "POST", body: JSON.stringify({ issuers: issuers ?? null }) }),
  runRefresh: (data: any) => req<any>("/api/run/refresh", { method: "POST", body: JSON.stringify(data) }),
};
