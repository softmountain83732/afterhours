-- ============================================================================
-- AFTERHOURS DATABASE MIGRATION 002
-- Vault Listener, Checkpoints, and Ingestion Idempotency
-- ============================================================================

-- 1. Vault synchronization checkpoint table
create table if not exists public.vault_sync_checkpoints (
  chat_id                 bigint primary key,
  last_scanned_message_id bigint not null default 0,
  last_sync_at            timestamptz not null default now(),
  created_at              timestamptz not null default now()
);

-- Enable Row-Level Security
alter table public.vault_sync_checkpoints enable row level security;

-- Safely clean up existing policies for idempotent re-runs
drop policy if exists "Service role full access on vault_sync_checkpoints" on public.vault_sync_checkpoints;
drop policy if exists "Public read on vault_sync_checkpoints" on public.vault_sync_checkpoints;

-- Service role full access (read & write restricted strictly to backend service role)
create policy "Service role full access on vault_sync_checkpoints"
  on public.vault_sync_checkpoints
  for all
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');

-- 2. Partial unique index on (telegram_chat_id, telegram_message_id) for idempotent ingestion
create unique index if not exists tracks_telegram_msg_unique_idx
  on public.tracks (telegram_chat_id, telegram_message_id)
  where telegram_chat_id is not null and telegram_message_id is not null;
