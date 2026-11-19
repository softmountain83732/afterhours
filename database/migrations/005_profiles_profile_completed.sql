-- ============================================================================
-- Migration 005: Add profile_completed column to profiles
-- ============================================================================

-- 1. Add profile_completed boolean column with default false
ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS profile_completed boolean NOT NULL DEFAULT false;

-- 2. Backfill existing registered profiles:
-- Any profile that has non-empty bio or non-null gender or non-null avatar is marked as completed
UPDATE public.profiles
SET profile_completed = true
WHERE (bio IS NOT NULL AND bio != '')
   OR gender IS NOT NULL
   OR avatar_url IS NOT NULL;

-- Also mark any existing non-guest profile that has both username and display_name as completed
UPDATE public.profiles
SET profile_completed = true
WHERE is_guest = false
  AND username IS NOT NULL
  AND display_name IS NOT NULL
  AND username NOT LIKE 'user_%';
