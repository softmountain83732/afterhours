-- ============================================================================
-- Migration 005 Down: Remove profile_completed column from profiles
-- ============================================================================

ALTER TABLE public.profiles
DROP COLUMN IF EXISTS profile_completed;
