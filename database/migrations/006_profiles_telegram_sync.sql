-- ============================================================================
-- Migration 006: Add telegram_user_id and Telegram profile sync fields
-- ============================================================================

-- 1. Add Telegram profile sync columns to public.profiles
ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS telegram_user_id bigint,
ADD COLUMN IF NOT EXISTS first_name text,
ADD COLUMN IF NOT EXISTS last_name text,
ADD COLUMN IF NOT EXISTS language_code text,
ADD COLUMN IF NOT EXISTS is_bot boolean NOT NULL DEFAULT false;

-- 2. Backfill telegram_user_id from existing telegram_id
UPDATE public.profiles
SET telegram_user_id = telegram_id
WHERE telegram_user_id IS NULL AND telegram_id IS NOT NULL;

-- 3. Create unique index on telegram_user_id (where not null)
CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_telegram_user_id
ON public.profiles(telegram_user_id)
WHERE telegram_user_id IS NOT NULL;
