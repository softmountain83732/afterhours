-- Rollback Migration: 003_listening_rooms_jam_sync_down.sql
-- Description: Revert Spotify Jam-Style Listening Room Sync & Normalized Queue

BEGIN;

-- 1. Remove tables from Supabase Realtime publication if present
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        IF EXISTS (
            SELECT 1 FROM pg_publication_tables 
            WHERE pubname = 'supabase_realtime' AND tablename = 'room_queue'
        ) THEN
            ALTER PUBLICATION supabase_realtime DROP TABLE public.room_queue;
        END IF;

        IF EXISTS (
            SELECT 1 FROM pg_publication_tables 
            WHERE pubname = 'supabase_realtime' AND tablename = 'room_members'
        ) THEN
            ALTER PUBLICATION supabase_realtime DROP TABLE public.room_members;
        END IF;

        IF EXISTS (
            SELECT 1 FROM pg_publication_tables 
            WHERE pubname = 'supabase_realtime' AND tablename = 'rooms'
        ) THEN
            ALTER PUBLICATION supabase_realtime DROP TABLE public.rooms;
        END IF;
    END IF;
END $$;

-- 2. Drop audit log, votes, and queue tables
DROP TABLE IF EXISTS public.room_sync_audit_log CASCADE;
DROP TABLE IF EXISTS public.room_queue_votes CASCADE;
DROP TABLE IF EXISTS public.room_queue CASCADE;

-- 3. Revert room_members additions
ALTER TABLE public.room_members
    DROP CONSTRAINT IF EXISTS room_members_role_check,
    DROP COLUMN IF EXISTS can_moderate,
    DROP COLUMN IF EXISTS can_invite,
    DROP COLUMN IF EXISTS role;

-- 4. Revert rooms table additions
ALTER TABLE public.rooms
    DROP COLUMN IF EXISTS updated_by,
    DROP COLUMN IF EXISTS scheduled_start_ms,
    DROP COLUMN IF EXISTS server_timestamp,
    DROP COLUMN IF EXISTS playback_rate,
    DROP COLUMN IF EXISTS revision;

COMMIT;
