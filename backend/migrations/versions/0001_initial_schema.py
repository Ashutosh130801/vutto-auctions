"""Initial schema.

Creates the full domain: users & refresh-token families, bike catalogue,
auctions, the hash-chained bid ledger, deposit accounts/holds, the
transactional outbox, audit log, notifications and idempotency records.

Revision ID: 0001_initial
Revises:
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('outbox_events',
    sa.Column('aggregate_type', sa.String(length=48), nullable=False),
    sa.Column('aggregate_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox_events'))
    )
    op.create_index('ix_outbox_aggregate', 'outbox_events', ['aggregate_type', 'aggregate_id'], unique=False)
    op.create_index('ix_outbox_pending', 'outbox_events', ['created_at'], unique=False, postgresql_where=sa.text('dispatched_at IS NULL'))
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('full_name', sa.String(length=160), nullable=False),
    sa.Column('phone', sa.String(length=24), nullable=True),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('BUYER', 'ADMIN', name='user_role'), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'ACTIVE', 'SUSPENDED', name='user_status'), nullable=False),
    sa.Column('kyc_verified', sa.Boolean(), nullable=False),
    sa.Column('token_version', sa.Integer(), nullable=False),
    sa.Column('failed_login_attempts', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index('ix_users_role_status', 'users', ['role', 'status'], unique=False)
    op.create_table('audit_logs',
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('actor_email', sa.String(length=320), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=48), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_audit_logs_actor_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index('ix_audit_entity', 'audit_logs', ['entity_type', 'entity_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_table('bikes',
    sa.Column('registration_number', sa.String(length=20), nullable=False),
    sa.Column('make', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=96), nullable=False),
    sa.Column('variant', sa.String(length=96), nullable=True),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('engine_cc', sa.Integer(), nullable=False),
    sa.Column('odometer_km', sa.Integer(), nullable=False),
    sa.Column('fuel_type', sa.Enum('PETROL', 'ELECTRIC', 'HYBRID', name='fuel_type'), nullable=False),
    sa.Column('colour', sa.String(length=48), nullable=True),
    sa.Column('owners_count', sa.Integer(), nullable=False),
    sa.Column('city', sa.String(length=64), nullable=False),
    sa.Column('condition_grade', sa.String(length=2), nullable=False),
    sa.Column('inspection_score', sa.Integer(), nullable=False),
    sa.Column('inspection', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('images', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('estimated_value', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'READY', 'IN_AUCTION', 'SOLD', 'WITHDRAWN', name='bike_status'), nullable=False),
    sa.Column('seller_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("condition_grade IN ('A','B','C','D')", name=op.f('ck_bikes_condition_grade_valid')),
    sa.CheckConstraint('inspection_score BETWEEN 0 AND 100', name=op.f('ck_bikes_inspection_score_range')),
    sa.CheckConstraint('odometer_km >= 0', name=op.f('ck_bikes_odometer_non_negative')),
    sa.CheckConstraint('year BETWEEN 1980 AND 2100', name=op.f('ck_bikes_year_sane')),
    sa.ForeignKeyConstraint(['seller_id'], ['users.id'], name=op.f('fk_bikes_seller_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bikes')),
    sa.UniqueConstraint('registration_number', name=op.f('uq_bikes_registration_number'))
    )
    op.create_index(op.f('ix_bikes_city'), 'bikes', ['city'], unique=False)
    op.create_index(op.f('ix_bikes_make'), 'bikes', ['make'], unique=False)
    op.create_index(op.f('ix_bikes_model'), 'bikes', ['model'], unique=False)
    op.create_index('ix_bikes_search', 'bikes', ['make', 'model', 'year', 'city'], unique=False)
    op.create_index(op.f('ix_bikes_year'), 'bikes', ['year'], unique=False)
    op.create_table('deposit_accounts',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('balance', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('held', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('balance >= 0', name=op.f('ck_deposit_accounts_balance_non_negative')),
    sa.CheckConstraint('held <= balance', name=op.f('ck_deposit_accounts_held_within_balance')),
    sa.CheckConstraint('held >= 0', name=op.f('ck_deposit_accounts_held_non_negative')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_deposit_accounts_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_deposit_accounts'))
    )
    op.create_table('idempotency_records',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('endpoint', sa.String(length=120), nullable=False),
    sa.Column('request_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('response', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_idempotency_records_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'key', name=op.f('pk_idempotency_records'))
    )
    op.create_index('ix_idempotency_created', 'idempotency_records', ['created_at'], unique=False)
    op.create_table('notifications',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.Enum('OUTBID', 'AUCTION_STARTING', 'AUCTION_EXTENDED', 'AUCTION_WON', 'AUCTION_LOST', 'RESERVE_NOT_MET', name='notification_type'), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('body', sa.String(length=400), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    op.create_index('ix_notifications_user_unread', 'notifications', ['user_id', 'created_at'], unique=False, postgresql_where=sa.text('read_at IS NULL'))
    op.create_table('refresh_tokens',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('family_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_agent', sa.String(length=256), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_refresh_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens'))
    )
    op.create_index(op.f('ix_refresh_tokens_created_at'), 'refresh_tokens', ['created_at'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_family_id'), 'refresh_tokens', ['family_id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_token_hash'), 'refresh_tokens', ['token_hash'], unique=True)
    op.create_index('ix_refresh_tokens_user_active', 'refresh_tokens', ['user_id', 'revoked_at'], unique=False)
    op.create_table('auctions',
    sa.Column('bike_id', sa.UUID(), nullable=False),
    sa.Column('slug', sa.String(length=160), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('SCHEDULED', 'LIVE', 'ENDED', 'SETTLED', 'CANCELLED', name='auction_status'), nullable=False),
    sa.Column('outcome', sa.Enum('PENDING', 'SOLD', 'RESERVE_NOT_MET', 'NO_BIDS', 'CANCELLED', name='auction_outcome'), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('scheduled_ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('anti_snipe_window_seconds', sa.Integer(), nullable=False),
    sa.Column('anti_snipe_extension_seconds', sa.Integer(), nullable=False),
    sa.Column('anti_snipe_max_extensions', sa.Integer(), nullable=False),
    sa.Column('extension_count', sa.Integer(), nullable=False),
    sa.Column('start_price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('reserve_price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('bid_increment', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('deposit_required', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('current_price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('leading_bid_id', sa.UUID(), nullable=True),
    sa.Column('leading_user_id', sa.UUID(), nullable=True),
    sa.Column('bid_count', sa.Integer(), nullable=False),
    sa.Column('bidder_count', sa.Integer(), nullable=False),
    sa.Column('last_bid_sequence', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('winner_id', sa.UUID(), nullable=True),
    sa.Column('winning_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('bid_increment > 0', name=op.f('ck_auctions_increment_positive')),
    sa.CheckConstraint('current_price >= 0', name=op.f('ck_auctions_current_price_non_negative')),
    sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_auctions_ends_after_starts')),
    sa.CheckConstraint('reserve_price IS NULL OR reserve_price >= start_price', name=op.f('ck_auctions_reserve_ge_start')),
    sa.CheckConstraint('start_price >= 0', name=op.f('ck_auctions_start_price_non_negative')),
    sa.ForeignKeyConstraint(['bike_id'], ['bikes.id'], name=op.f('fk_auctions_bike_id_bikes'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_auctions_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['leading_user_id'], ['users.id'], name=op.f('fk_auctions_leading_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['winner_id'], ['users.id'], name=op.f('fk_auctions_winner_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auctions'))
    )
    op.create_index(op.f('ix_auctions_slug'), 'auctions', ['slug'], unique=True)
    op.create_index(op.f('ix_auctions_status'), 'auctions', ['status'], unique=False)
    op.create_index('ix_auctions_status_ends_at', 'auctions', ['status', 'ends_at'], unique=False)
    op.create_index('ix_auctions_status_starts_at', 'auctions', ['status', 'starts_at'], unique=False)
    op.create_index('uq_bikes_one_open_auction', 'auctions', ['bike_id'], unique=True, postgresql_where=sa.text("status IN ('SCHEDULED','LIVE')"))
    op.create_table('bids',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('auction_id', sa.UUID(), nullable=False),
    sa.Column('bidder_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('max_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('status', sa.Enum('LEADING', 'OUTBID', 'WON', 'LOST', name='bid_status'), nullable=False),
    sa.Column('source', sa.Enum('MANUAL', 'PROXY', name='bid_source'), nullable=False),
    sa.Column('is_winning', sa.Boolean(), nullable=False),
    sa.Column('extended_auction', sa.Boolean(), nullable=False),
    sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=256), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('prev_hash', sa.String(length=64), nullable=False),
    sa.Column('entry_hash', sa.String(length=64), nullable=False),
    sa.CheckConstraint('amount > 0', name=op.f('ck_bids_amount_positive')),
    sa.CheckConstraint('max_amount >= amount', name=op.f('ck_bids_max_ge_amount')),
    sa.ForeignKeyConstraint(['auction_id'], ['auctions.id'], name=op.f('fk_bids_auction_id_auctions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['bidder_id'], ['users.id'], name=op.f('fk_bids_bidder_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bids')),
    sa.UniqueConstraint('auction_id', 'sequence', name='uq_bids_auction_sequence'),
    sa.UniqueConstraint('bidder_id', 'idempotency_key', name='uq_bids_bidder_idempotency')
    )
    op.create_index('ix_bids_auction_seq_desc', 'bids', ['auction_id', 'sequence'], unique=False)
    op.create_index('ix_bids_bidder_placed', 'bids', ['bidder_id', 'placed_at'], unique=False)
    op.create_index('ix_bids_auction_bidder', 'bids', ['auction_id', 'bidder_id'], unique=False)
    op.create_table('deposit_holds',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('auction_id', sa.UUID(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'RELEASED', 'CAPTURED', name='hold_status'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['auction_id'], ['auctions.id'], name=op.f('fk_deposit_holds_auction_id_auctions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_deposit_holds_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_deposit_holds'))
    )
    op.create_index('uq_deposit_holds_active', 'deposit_holds', ['user_id', 'auction_id'], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"))
    op.create_table('deposit_transactions',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.Enum('TOPUP', 'REFUND', 'HOLD', 'RELEASE', 'CAPTURE', name='deposit_txn_type'), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('auction_id', sa.UUID(), nullable=True),
    sa.Column('reference', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['auction_id'], ['auctions.id'], name=op.f('fk_deposit_transactions_auction_id_auctions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_deposit_transactions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_deposit_transactions'))
    )
    op.create_index('ix_deposit_txn_user_created', 'deposit_transactions', ['user_id', 'created_at'], unique=False)
    op.create_table('watchlist',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('auction_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['auction_id'], ['auctions.id'], name=op.f('fk_watchlist_auction_id_auctions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_watchlist_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'auction_id', name=op.f('pk_watchlist'))
    )

    # Case-insensitive uniqueness on email.  We store the address as typed (so
    # we can display it back faithfully) but forbid two accounts differing only
    # by case, which is a classic account-takeover vector.
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_email_lower")
    op.drop_table('watchlist')
    op.drop_index('ix_deposit_txn_user_created', table_name='deposit_transactions')
    op.drop_table('deposit_transactions')
    op.drop_index('uq_deposit_holds_active', table_name='deposit_holds', postgresql_where=sa.text("status = 'ACTIVE'"))
    op.drop_table('deposit_holds')
    op.drop_index('ix_bids_auction_bidder', table_name='bids')
    op.drop_index('ix_bids_bidder_placed', table_name='bids')
    op.drop_index('ix_bids_auction_seq_desc', table_name='bids')
    op.drop_table('bids')
    op.drop_index('uq_bikes_one_open_auction', table_name='auctions', postgresql_where=sa.text("status IN ('SCHEDULED','LIVE')"))
    op.drop_index('ix_auctions_status_starts_at', table_name='auctions')
    op.drop_index('ix_auctions_status_ends_at', table_name='auctions')
    op.drop_index(op.f('ix_auctions_status'), table_name='auctions')
    op.drop_index(op.f('ix_auctions_slug'), table_name='auctions')
    op.drop_table('auctions')
    op.drop_index('ix_refresh_tokens_user_active', table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_token_hash'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_family_id'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_created_at'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_notifications_user_unread', table_name='notifications', postgresql_where=sa.text('read_at IS NULL'))
    op.drop_table('notifications')
    op.drop_index('ix_idempotency_created', table_name='idempotency_records')
    op.drop_table('idempotency_records')
    op.drop_table('deposit_accounts')
    op.drop_index(op.f('ix_bikes_year'), table_name='bikes')
    op.drop_index('ix_bikes_search', table_name='bikes')
    op.drop_index(op.f('ix_bikes_model'), table_name='bikes')
    op.drop_index(op.f('ix_bikes_make'), table_name='bikes')
    op.drop_index(op.f('ix_bikes_city'), table_name='bikes')
    op.drop_table('bikes')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_index('ix_audit_entity', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index('ix_users_role_status', table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index('ix_outbox_pending', table_name='outbox_events', postgresql_where=sa.text('dispatched_at IS NULL'))
    op.drop_index('ix_outbox_aggregate', table_name='outbox_events')
    op.drop_table('outbox_events')

    # Alembic's autogenerated downgrade drops the tables but *not* the native
    # PostgreSQL ENUM types that SQLAlchemy created implicitly alongside them.
    # Leaving them behind makes the migration irreversible: the next `upgrade`
    # fails with `type "user_role" already exists`.  Dropping them here is what
    # makes `upgrade -> downgrade -> upgrade` actually work, which CI asserts.
    op.execute("DROP TYPE IF EXISTS user_role")
    op.execute("DROP TYPE IF EXISTS user_status")
    op.execute("DROP TYPE IF EXISTS fuel_type")
    op.execute("DROP TYPE IF EXISTS bike_status")
    op.execute("DROP TYPE IF EXISTS auction_status")
    op.execute("DROP TYPE IF EXISTS auction_outcome")
    op.execute("DROP TYPE IF EXISTS bid_status")
    op.execute("DROP TYPE IF EXISTS bid_source")
    op.execute("DROP TYPE IF EXISTS hold_status")
    op.execute("DROP TYPE IF EXISTS deposit_txn_type")
    op.execute("DROP TYPE IF EXISTS notification_type")
