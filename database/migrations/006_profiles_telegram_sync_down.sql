-- ============================================================================
-- Migration 006 Rollback: Remove telegram_user_id and Telegram sync fields
-- ============================================================================

DROP INDEX IF EXISTS public.idx_profiles_telegram_user_id;

ALTER TABLE public.profiles
DROP COLUMN IF EXISTS telegram_user_id,
DROP COLUMN IF EXISTS first_name,
DROP COLUMN IF EXISTS last_name,
DROP COLUMN IF EXISTS language_code,
DROP COLUMN IF EXISTS is_bot;
