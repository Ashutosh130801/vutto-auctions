export type AuctionStatus = 'SCHEDULED' | 'LIVE' | 'ENDED' | 'SETTLED' | 'CANCELLED'
export type AuctionOutcome = 'PENDING' | 'SOLD' | 'RESERVE_NOT_MET' | 'NO_BIDS' | 'CANCELLED'
export type BidStatus = 'LEADING' | 'OUTBID' | 'WON' | 'LOST'
export type Role = 'BUYER' | 'ADMIN'

export interface User {
  id: string
  email: string
  full_name: string
  phone: string | null
  role: Role
  status: 'PENDING' | 'ACTIVE' | 'SUSPENDED'
  kyc_verified: boolean
  created_at: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_at: string
  user: User
}

export interface AuctionSummary {
  id: string
  slug: string
  title: string
  status: AuctionStatus
  outcome: AuctionOutcome
  starts_at: string
  ends_at: string
  current_price: string
  start_price: string
  bid_increment: string
  deposit_required: string
  bid_count: number
  bidder_count: number
  version: number
  has_reserve: boolean
  reserve_met: boolean
  minimum_next_bid: string
  thumbnail: string | null
  city: string | null
  make: string | null
  model: string | null
  year: number | null
}

export interface Bike {
  id: string
  registration_number: string
  make: string
  model: string
  variant: string | null
  year: number
  engine_cc: number
  odometer_km: number
  fuel_type: string
  colour: string | null
  owners_count: number
  city: string
  condition_grade: string
  inspection_score: number
  inspection: Record<string, unknown>
  images: string[]
  description: string | null
  estimated_value: string
  status: string
}

export interface AuctionDetail extends AuctionSummary {
  notes: string | null
  scheduled_ends_at: string
  extension_count: number
  anti_snipe_window_seconds: number
  anti_snipe_extension_seconds: number
  anti_snipe_max_extensions: number
  closed_at: string | null
  winning_amount: string | null
  bike: Bike
  your_max_bid: string | null
  you_are_leading: boolean
  you_are_watching: boolean
}

export interface Bid {
  id: string
  sequence: number
  amount: string
  status: BidStatus
  source: 'MANUAL' | 'PROXY'
  bidder_alias: string
  bidder_id: string
  placed_at: string
  entry_hash: string
  is_you: boolean
}

export interface BidAccepted {
  bid_id: string
  sequence: number
  verdict: 'LEAD_TAKEN' | 'OUTBID_IMMEDIATELY' | 'LEAD_RAISED'
  is_leading: boolean
  current_price: string
  minimum_next_bid: string
  your_max: string
  extended: boolean
  ends_at: string
  reserve_met: boolean
  auction_version: number
  entry_hash: string
}

export interface Deposit { balance: string; held: string; available: string }

export interface Notification {
  id: string
  type: string
  title: string
  body: string
  data: Record<string, string>
  read_at: string | null
  created_at: string
}

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export interface AdminStats {
  live_auctions: number
  scheduled_auctions: number
  ended_auctions: number
  ending_within_hour: number
  total_bids: number
  gross_merchandise_value: number
  total_users: number
}

export interface LedgerVerdict {
  valid: boolean
  entries_checked: number
  head_hash: string | null
  broken_at_sequence: number | null
  reason: string | null
}

/** The realtime frame shape. Every frame carries `server_time`. */
export interface RealtimeFrame {
  event: string
  data: Record<string, any>
  server_time: string
  event_id?: string
}
