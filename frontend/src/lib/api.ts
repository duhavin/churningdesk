// Thin typed client over the FastAPI backend. All paths are relative; the Vite
// dev server proxies /api to the configured backend, and production serves both.

function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return path.startsWith("/") ? path : `/${path}`;
}

async function req<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(apiUrl(path), {
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
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    const body = await res.text();
    const returnedHtml = body.trim().startsWith("<") || body.includes("<div id=\"root\"");
    throw new Error(
      returnedHtml
        ? `API route ${path} returned the frontend app instead of JSON. Check the dev proxy/backend target.`
        : `API route ${path} returned ${contentType || "non-JSON"} instead of JSON.`,
    );
  }
  return res.json();
}

// --- Types -----------------------------------------------------------------
export type JsonScalar = string | number | boolean | null;
export type JsonValue = JsonScalar | JsonValue[] | { [key: string]: JsonValue };
export type ApiPayload = Record<string, JsonValue | undefined>;

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
  crawl4ai_enabled?: boolean;
  crawl4ai_available?: boolean;
  crypto_available: boolean;
}
export interface RefreshJob extends RefreshResult {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  processed: number;
  total: number;
  skipped: number;
  current_product: string | null;
  committed: number;
  proposed: number;
  errors: RunNotice[];
  warnings: RunNotice[];
  phase: string;
  result: RefreshResult | null;
}
export interface RunNotice {
  product?: string;
  code?: string;
  error?: string;
  detail?: string;
  message?: string;
  warning?: string;
  [key: string]: JsonValue | undefined;
}
export interface RefreshProductResult {
  id: number;
  issuer: string;
  product_name: string;
  engine?: string;
  result: {
    committed?: string[];
    proposed?: string[];
    note?: string | null;
    source_url?: string | null;
    scan_confidence?: number | null;
    offer_status?: string | null;
  };
  scan_row?: ApiPayload;
}
export interface RefreshResult {
  refreshed_count?: number;
  products_checked?: number;
  products_skipped?: number;
  products_pending_review?: number;
  products_deferred_by_limit?: number;
  committed?: number;
  proposed?: number;
  errors?: RunNotice[];
  warnings?: RunNotice[];
  results?: RefreshProductResult[];
  rows?: ApiPayload[];
  scan?: ApiPayload;
  peaks_filled?: number;
  valuations_added?: number;
  review_cleanup?: ApiPayload;
  source_cleanup?: ApiPayload;
}
export interface RefreshRunResponse extends RefreshResult {
  status?: "started" | "already_running" | string;
  job?: RefreshJob;
}
export interface DiscoveryResult {
  discovered_count: number;
  added_count: number;
  web_error?: string | null;
  [key: string]: JsonValue | undefined;
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
  display_name?: string;
  canonical_key?: string;
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
  earn_multipliers: Record<string, number | string> | null;
  best_category_uses: Record<string, string> | null;
  card_benefits: BenefitDefinition[] | null;
  card_protections: string[] | null;
  downgrade_paths: JsonValue[] | null;
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
  is_exceptional: boolean;
  decision_ready?: boolean;
  data_quality_issues?: string[];
  status: string;
  rank: number | null;
}
export interface CardReference {
  id: number;
  canonical_key: string;
  issuer: string;
  product_name: string;
  display_name: string;
  aliases?: string[];
  search_terms?: string[];
  currency?: string | null;
  product_family?: string | null;
  ownership?: string | null;
  account_type?: string | null;
  reports_to_personal_credit?: boolean | null;
  issuer_url: string | null;
  offer_url: string | null;
  active: boolean;
}
export interface HeldCard {
  id: number;
  user: string;
  product_id: number | null;
  issuer: string;
  product_name: string;
  display_name?: string;
  canonical_key?: string;
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
export interface BenefitDefinition {
  name?: string;
  value?: string | number | null;
  frequency?: string | null;
  category?: string | null;
  description?: string | null;
  evidence?: string | null;
  confidence?: number | null;
}
export interface Five24 {
  count: number;
  under_524: boolean;
  earliest_drop_date: string | null;
  contributing?: { issuer: string; product_name: string; display_name?: string; date_opened: string }[];
}
export interface CategoryCandidate {
  needs_data?: boolean;
  reason?: string;
  user: string;
  card_id: number;
  product_id: number;
  issuer: string;
  product_name: string;
  display_name: string;
  currency: string | null;
  multiplier: number | null;
  cpp: number | null;
  effective_rate_cents: number | null;
  value_per_dollar: number | null;
  note: string | null;
  source: string | null;
  source_category: string | null;
  is_fallback: boolean;
  fallback_reason: string | null;
}
export interface CategoryGuideItem {
  category: string;
  status: "covered" | "needs_data";
  winner: CategoryCandidate | null;
  runner_up: CategoryCandidate | null;
  needs_data_reason: string | null;
  needs_data_candidates: CategoryCandidate[];
}
export interface CategoryGuide {
  user?: string | null;
  scope?: string;
  categories: CategoryGuideItem[];
}
export interface CategoryCoverageItem {
  category: string;
  covered: boolean;
  issuer: string | null;
  product_name: string | null;
  display_name: string | null;
  product_id: number | null;
  multiplier: number | null;
  cpp?: number | null;
  effective_rate_cents?: number | null;
  value_per_dollar?: number | null;
  note: string | null;
  source_category: string | null;
  is_fallback: boolean;
  fallback_reason: string | null;
  needs_data_reason?: string | null;
}
export interface BenefitRow {
  row_key: string;
  usage_id: number | null;
  user: string;
  held_card_id: number;
  product_id: number;
  issuer: string;
  product_name: string;
  display_name: string;
  benefit_key: string;
  benefit_name: string;
  benefit_label?: string;
  description?: string | null;
  cadence: string;
  period_key: string;
  period_start: string;
  period_end: string;
  due_date: string;
  timeframe_note?: string | null;
  amount_available: number | null;
  amount_used: number | null;
  amount_remaining: number | null;
  amount_source: string;
  points_amount: number | null;
  points_unit: string | null;
  display_value: string | null;
  is_anniversary: boolean;
  progress: number | null;
  status: string;
  status_label: string;
  priority: string;
  action_label: string;
  days_remaining: number | null;
  tracking_kind: string;
  notes: string | null;
  suppressed?: boolean;
  suppression_scope?: string | null;
  source_url: string | null;
  last_verified: string | null;
  verified_status: string;
}
export interface BenefitMissing {
  user?: string;
  card_id?: number;
  held_card_id?: number;
  product_id?: number;
  issuer: string;
  product_name: string;
  display_name: string;
  reason: string;
  source_url?: string | null;
  last_verified?: string | null;
  verified_status?: string | null;
}
export interface BenefitUserSnapshot {
  user: string;
  has_card: boolean;
  held_card_id: number | null;
  status: string;
  status_label: string;
  action_label: string;
  amount_available: number | null;
  amount_used: number | null;
  amount_remaining: number | null;
  progress: number | null;
  display_value: string | null;
  points_amount?: number | null;
  points_unit?: string | null;
  amount_source?: string | null;
  tracking_kind?: string | null;
  due_date: string | null;
  days_remaining: number | null;
  timeframe_note?: string | null;
  suppressed?: boolean;
  suppression_scope?: string | null;
  verified_status: string | null;
}
export interface BenefitTrackerGroup {
  group_key: string;
  issuer: string;
  product_name: string;
  display_name: string;
  benefit_key: string;
  benefit_label: string;
  benefit_name: string;
  cadence: string;
  period_key: string;
  due_date: string;
  timeframe_note?: string | null;
  display_value: string | null;
  status: string;
  action_label: string;
  amount_available: number | null;
  amount_used: number | null;
  amount_remaining: number | null;
  progress: number | null;
  users: BenefitUserSnapshot[];
  source_url?: string | null;
  last_verified?: string | null;
}
export interface BenefitLedger {
  benefits: BenefitRow[];
  tracker_groups?: BenefitTrackerGroup[];
  missing: BenefitMissing[];
  summary: {
    total_benefits: number;
    known_annual_value: number;
    cards_missing_benefits: number;
    tracked_groups?: number;
    need_action?: number;
    known_available_value?: number;
    known_used_value?: number;
    known_remaining_value?: number;
  };
}
export interface ProfileSummary {
  user: string;
  point_balances: Record<string, number>;
  balance_breakdown: { currency: string; balance: number; cpp: number; value: number }[];
  total_est_value: number;
  five_24: Five24;
  held_count: number;
  category_coverage: CategoryCoverageItem[];
  benefit_tracker: BenefitLedger;
  notes: string | null;
}
export interface DashboardAttention {
  type: string;
  severity: "high" | "info" | string;
  card: string;
  due_date?: string | null;
  action: string;
  detail: string;
  message: string;
  benefit_name?: string;
  benefit_label?: string;
}
export interface DashboardResponse {
  user: string;
  held_cards: HeldCard[];
  five_24: Five24;
  needs_attention: DashboardAttention[];
}
export interface PipelineCard {
  rank?: number;
  id: number;
  issuer: string;
  product_name: string;
  display_name: string;
  ownership: string;
  currency: string | null;
  tag: string | null;
  status: string;
  peak_score: number;
  offer_value: number;
  effective_points: number;
  current_offer_min_spend: number | null;
  current_offer_window_months: number | null;
  targeted_beats_public: boolean;
  is_exceptional: boolean;
  decision_ready?: boolean;
  data_quality_issues?: string[];
  relationship?: string;
  reason: string;
}
export interface LadderAlternative {
  id: number;
  product_name: string;
  display_name?: string | null;
  offer_value: number;
  peak_score: number;
  status: string;
}
export interface HeldAction {
  id: number;
  issuer: string;
  product_name: string;
  display_name: string;
  status: string;
  annual_fee: number | null;
  renewal_date: string | null;
  bonus_eligible_again: boolean;
  eligible_again_date: string | null;
  action: string;
  reason: string;
  value_score: number;
  value_drivers: string[];
  downgrade_paths: JsonValue[] | null;
  household_overlap_users: string[];
  ladder_alternative: LadderAlternative | null;
  ladder_alternatives: LadderAlternative[];
}
export interface PipelineResponse {
  user: string;
  five_24: Five24;
  next_cards: PipelineCard[];
  needs_data: PipelineCard[];
  alternate_strategies: PipelineCard[];
  held_actions: HeldAction[];
}
export interface HouseholdUserSummary {
  user: string;
  under_524: boolean;
  five24_count: number;
  earliest_drop_date: string | null;
  total_est_value: number;
  held_count: number;
  annual_fees: number;
}
export interface BalanceRow {
  currency: string;
  by_user: Record<string, number>;
  combined: number;
}
export interface HouseholdReferral {
  from_user: string;
  to_user: string;
  id: number;
  issuer: string;
  product_name: string;
  display_name: string;
  currency: string | null;
  welcome_points: number | null;
  current_offer_points: number | null;
  referral_bonus_points: number | null;
  referral_bonus_cash: number | null;
  household_points: number;
  referral_value: number | null;
  recipient_offer_value: number;
  recipient_status: string;
  peak_score: number;
  household_gain: number;
  pipeline_rank?: number | null;
  is_exceptional?: boolean;
  decision_ready?: boolean;
  data_quality_issues?: string[];
  route?: string;
  referral_match?: string | null;
  reason: string;
}
export interface HouseholdMove {
  user: string;
  id: number;
  issuer: string;
  product_name: string;
  display_name: string;
  ownership: string;
  currency: string | null;
  status: string;
  peak_score: number;
  offer_value: number;
  first_year_value: number;
  welcome_points: number;
  referral_bonus_points: number;
  referral_bonus_cash: number;
  household_points: number;
  current_offer_points: number | null;
  current_offer_min_spend: number | null;
  current_offer_window_months: number | null;
  is_exceptional: boolean;
  household_value: number;
  pipeline_rank?: number | null;
  decision_ready?: boolean;
  data_quality_issues?: string[];
  route: string;
  referral_from: string | null;
  referral_match?: string | null;
  referral_value: number | null;
  reason: string;
}
export interface HouseholdResponse {
  users: HouseholdUserSummary[];
  combined_est_value: number;
  balance_rows: BalanceRow[];
  card_snapshot: Record<string, HeldCard[]>;
  referrals: HouseholdReferral[];
  moves: HouseholdMove[];
}
export interface ProposedChange {
  id: number;
  target_table: string;
  target_id: number;
  product: string | null;
  product_name: string | null;
  display_name: string | null;
  canonical_key: string | null;
  field: string;
  field_label: string;
  old_value: string | null;
  new_value: string | null;
  old_preview: string;
  new_preview: string;
  change_summary: string;
  review_note: string | null;
  reason_code: string | null;
  risk_level: string | null;
  quality_score: number | null;
  source_url: string | null;
  source_domain: string | null;
  confidence: number | null;
  status: string;
  created_at: string | null;
}
export interface CatalogHealthIssue {
  code: string;
  label: string;
  severity: "high" | "medium" | "low" | string;
}
export interface CatalogHealthProduct {
  product_id: number;
  issuer: string;
  product_name: string;
  display_name: string;
  currency: string | null;
  held_by: string[];
  status: "healthy" | "stale" | "needs_data" | "needs_review" | string;
  health_score: number;
  issues: CatalogHealthIssue[];
  next_action: string;
  source_url: string | null;
  last_verified: string | null;
  has_offer: boolean;
  has_public_peak: boolean;
  has_supplemental: boolean;
}
export interface CatalogHealthResponse {
  summary: {
    total: number;
    healthy: number;
    stale: number;
    needs_data: number;
    needs_review: number;
    held_needs_data: number;
  };
  products: CatalogHealthProduct[];
}
export interface RedemptionPlan {
  mode: "benchmark" | "live";
  live_enabled: boolean;
  note: string;
  balances: ApiPayload[];
  targets: ApiPayload[];
  transfer_partners: ApiPayload[];
  progress: ApiPayload[];
  best_uses: ApiPayload[];
}

export const api = {
  health: () => req<Health>("/api/health"),
  users: () => req<string[]>("/api/users"),
  runStatus: () => req<RunStatus>("/api/run/status"),
  refreshStatus: () => req<RefreshJob>("/api/run/refresh/status"),

  // Held cards
  cards: (user: string) => req<HeldCard[]>(`/api/cards?user=${encodeURIComponent(user)}`),
  createCard: (data: ApiPayload) => req<HeldCard>("/api/cards", { method: "POST", body: JSON.stringify(data) }),
  updateCard: (id: number, data: ApiPayload) =>
    req<HeldCard>(`/api/cards/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteCard: (id: number) => req(`/api/cards/${id}`, { method: "DELETE" }),

  // Dashboard
  dashboard: (user: string) => req<DashboardResponse>(`/api/dashboard?user=${encodeURIComponent(user)}`),

  // Catalog
  catalog: (user: string) => req<CatalogEntry[]>(`/api/catalog?user=${encodeURIComponent(user)}`),
  createProduct: (data: ApiPayload) => req<CatalogEntry>("/api/catalog", { method: "POST", body: JSON.stringify(data) }),
  updateProduct: (id: number, data: ApiPayload) =>
    req<CatalogEntry>(`/api/catalog/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteProduct: (id: number) => req(`/api/catalog/${id}`, { method: "DELETE" }),
  targetedOffer: (productId: number, user: string) =>
    req<ManualTargetedOffer | null>(`/api/catalog/${productId}/targeted-offer?user=${encodeURIComponent(user)}`),
  saveTargetedOffer: (productId: number, user: string, data: ApiPayload) =>
    req<ManualTargetedOffer>(`/api/catalog/${productId}/targeted-offer?user=${encodeURIComponent(user)}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteTargetedOffer: (productId: number, user: string) =>
    req(`/api/catalog/${productId}/targeted-offer?user=${encodeURIComponent(user)}`, { method: "DELETE" }),

  // Valuations
  valuations: () => req<ApiPayload[]>("/api/valuations"),
  upsertValuation: (data: ApiPayload) => req<ApiPayload>("/api/valuations", { method: "PUT", body: JSON.stringify(data) }),

  // Watchlist / blacklist
  watchlist: () => req<ApiPayload[]>("/api/watchlist"),
  addWatchlist: (data: ApiPayload) => req<ApiPayload>("/api/watchlist", { method: "POST", body: JSON.stringify(data) }),
  removeWatchlist: (id: number) => req(`/api/watchlist/${id}`, { method: "DELETE" }),
  blacklist: () => req<ApiPayload[]>("/api/blacklist"),
  addBlacklist: (data: ApiPayload) => req<ApiPayload>("/api/blacklist", { method: "POST", body: JSON.stringify(data) }),
  removeBlacklist: (id: number) => req(`/api/blacklist/${id}`, { method: "DELETE" }),

  // Profiles
  profiles: () => req<ProfileSummary[]>("/api/profiles"),
  profile: (user: string) => req<ProfileSummary>(`/api/profiles/${encodeURIComponent(user)}`),
  upsertProfile: (user: string, data: ApiPayload) =>
    req<ProfileSummary>(`/api/profiles/${encodeURIComponent(user)}`, { method: "PUT", body: JSON.stringify(data) }),
  saveBenefitUsage: (user: string, data: ApiPayload) =>
    req<BenefitLedger>(`/api/profiles/${encodeURIComponent(user)}/benefits`, { method: "PUT", body: JSON.stringify(data) }),

  // Pipeline
  pipeline: (user: string) => req<PipelineResponse>(`/api/pipeline?user=${encodeURIComponent(user)}`),

  // Household (combined two-user plan + referral synergy)
  household: () => req<HouseholdResponse>("/api/household"),
  categories: () => req<CategoryGuide>("/api/categories"),
  benefits: () => req<BenefitLedger>("/api/benefits"),
  catalogHealth: () => req<CatalogHealthResponse>("/api/catalog-health"),
  mergeCatalogDuplicates: () => req<ApiPayload>("/api/catalog/duplicates/merge", { method: "POST" }),
  sanitizeCatalogText: () => req<ApiPayload>("/api/catalog/sanitize-text", { method: "POST" }),
  cardReferences: () => req<CardReference[]>("/api/card-references"),

  // Redemption goals and transfer partners
  redemption: () => req<RedemptionPlan>("/api/redemption"),
  createRedemptionTarget: (data: ApiPayload) =>
    req<ApiPayload>("/api/redemption/targets", { method: "POST", body: JSON.stringify(data) }),
  updateRedemptionTarget: (id: number, data: ApiPayload) =>
    req<ApiPayload>(`/api/redemption/targets/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteRedemptionTarget: (id: number) => req<ApiPayload>(`/api/redemption/targets/${id}`, { method: "DELETE" }),
  createTransferPartner: (data: ApiPayload) =>
    req<ApiPayload>("/api/redemption/transfer-partners", { method: "POST", body: JSON.stringify(data) }),
  updateTransferPartner: (id: number, data: ApiPayload) =>
    req<ApiPayload>(`/api/redemption/transfer-partners/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteTransferPartner: (id: number) =>
    req<ApiPayload>(`/api/redemption/transfer-partners/${id}`, { method: "DELETE" }),

  // Ingestion admin
  proposedChanges: (status = "pending") => req<ProposedChange[]>(`/api/proposed-changes?status=${status}`),
  cleanupProposedChanges: () => req<ApiPayload>("/api/proposed-changes/cleanup", { method: "POST" }),
  decideChange: (id: number, status: string) =>
    req<ApiPayload>(`/api/proposed-changes/${id}/decision`, { method: "POST", body: JSON.stringify({ status }) }),
  discovered: () => req<ApiPayload[]>("/api/discovered"),
  reviewDiscovered: (id: number) => req<ApiPayload>(`/api/discovered/${id}/review`, { method: "POST" }),
  sources: () => req<ApiPayload>("/api/sources"),
  createSource: (data: ApiPayload) => req<ApiPayload>("/api/sources", { method: "POST", body: JSON.stringify(data) }),
  updateSource: (id: number, data: ApiPayload) =>
    req<ApiPayload>(`/api/sources/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteSource: (id: number) => req<ApiPayload>(`/api/sources/${id}`, { method: "DELETE" }),

  // Run menu
  runDiscover: (issuers?: string[]) =>
    req<DiscoveryResult>("/api/run/discover", { method: "POST", body: JSON.stringify({ issuers: issuers ?? null }) }),
  runRefresh: (data: ApiPayload) => req<RefreshRunResponse>("/api/run/refresh", { method: "POST", body: JSON.stringify(data) }),
};
