-- Migration: 003_listening_rooms_jam_sync.sql
-- Description: Spotify Jam-Style Listening Room Sync & Normalized Queue

BEGIN;

-- 1. Extend rooms table with sync metadata and monotonic revision
ALTER TABLE public.rooms
    ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS playback_rate NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    ADD COLUMN IF NOT EXISTS server_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS scheduled_start_ms BIGINT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL;

-- 2. Extend room_members with custom guest permissions and roles
ALTER TABLE public.room_members
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'listener',
    ADD COLUMN IF NOT EXISTS can_invite BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS can_moderate BOOLEAN NOT NULL DEFAULT false;

-- Add role check constraint if it does not already exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'room_members_role_check'
    ) THEN
        ALTER TABLE public.room_members
            ADD CONSTRAINT room_members_role_check 
            CHECK (role IN ('host', 'dj', 'queue_manager', 'listener', 'moderator'));
    END IF;
END $$;

-- 3. Create normalized room_queue table (single source of truth for queue)
CREATE TABLE IF NOT EXISTS public.room_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES public.rooms(id) ON DELETE CASCADE,
    track_id UUID NOT NULL REFERENCES public.tracks(id) ON DELETE CASCADE,
    added_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    vote_count INTEGER NOT NULL DEFAULT 0,
    played_at TIMESTAMPTZ DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_room_queue_room_pending 
    ON public.room_queue (room_id, position) 
    WHERE played_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_room_queue_track_id 
    ON public.room_queue (track_id);

-- 4. Create room_queue_votes table for collaborative voting
CREATE TABLE IF NOT EXISTS public.room_queue_votes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    queue_item_id UUID NOT NULL REFERENCES public.room_queue(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    vote INTEGER NOT NULL CHECK (vote IN (1, -1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (queue_item_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_room_queue_votes_item_id 
    ON public.room_queue_votes (queue_item_id);

-- 5. Create room_sync_audit_log table for tracking actions
CREATE TABLE IF NOT EXISTS public.room_sync_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES public.rooms(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    revision BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_room_audit_room_id 
    ON public.room_sync_audit_log (room_id, created_at DESC);

-- 6. Enable Supabase Realtime publication on room tables safely
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables 
            WHERE pubname = 'supabase_realtime' AND tablename = 'rooms'
        ) THEN
            ALTER PUBLICATION supabase_realtime ADD TABLE public.rooms;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables 
            WHERE pubname = 'supabase_realtime' AND tablename = 'room_members'
        ) THEN
            ALTER PUBLICATION supabase_realtime ADD TABLE public.room_members;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables 
            WHERE pubname = 'supabase_realtime' AND tablename = 'room_queue'
        ) THEN
            ALTER PUBLICATION supabase_realtime ADD TABLE public.room_queue;
        END IF;
    END IF;
END $$;

-- 7. Row Level Security (RLS) Policies
ALTER TABLE public.room_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.room_queue_votes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.room_sync_audit_log ENABLE ROW LEVEL SECURITY;

-- Helper functions exist from 001_initial_schema.sql: is_room_member(room_uuid, user_uuid), is_room_host(room_uuid, user_uuid)

-- Room Queue Policies
DROP POLICY IF EXISTS "room_queue: select if room member or public" ON public.room_queue;
CREATE POLICY "room_queue: select if room member or public" ON public.room_queue
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.rooms r
            WHERE r.id = room_queue.room_id
              AND (r.is_public = true OR r.host_id = auth.uid() OR is_room_member(r.id, auth.uid()))
        )
    );

DROP POLICY IF EXISTS "room_queue: insert authorized member" ON public.room_queue;
CREATE POLICY "room_queue: insert authorized member" ON public.room_queue
    FOR INSERT TO authenticated
    WITH CHECK (
        added_by = auth.uid() AND (
            is_room_host(room_id, auth.uid()) OR
            EXISTS (
                SELECT 1 FROM public.room_members m
                WHERE m.room_id = room_queue.room_id
                  AND m.user_id = auth.uid()
                  AND (m.can_manage_queue = true OR m.role IN ('dj', 'queue_manager', 'moderator'))
            )
        )
    );

DROP POLICY IF EXISTS "room_queue: delete authorized member" ON public.room_queue;
CREATE POLICY "room_queue: delete authorized member" ON public.room_queue
    FOR DELETE TO authenticated
    USING (
        is_room_host(room_id, auth.uid()) OR
        EXISTS (
            SELECT 1 FROM public.room_members m
            WHERE m.room_id = room_queue.room_id
              AND m.user_id = auth.uid()
              AND (m.can_manage_queue = true OR m.role IN ('dj', 'queue_manager', 'moderator'))
        )
    );

-- Room Queue Votes Policies
DROP POLICY IF EXISTS "room_queue_votes: select room members" ON public.room_queue_votes;
CREATE POLICY "room_queue_votes: select room members" ON public.room_queue_votes
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.room_queue q
            JOIN public.rooms r ON r.id = q.room_id
            WHERE q.id = room_queue_votes.queue_item_id
              AND (r.is_public = true OR r.host_id = auth.uid() OR is_room_member(r.id, auth.uid()))
        )
    );

DROP POLICY IF EXISTS "room_queue_votes: insert own vote" ON public.room_queue_votes;
CREATE POLICY "room_queue_votes: insert own vote" ON public.room_queue_votes
    FOR INSERT TO authenticated
    WITH CHECK (
        user_id = auth.uid() AND
        EXISTS (
            SELECT 1 FROM public.room_queue q
            JOIN public.rooms r ON r.id = q.room_id
            WHERE q.id = room_queue_votes.queue_item_id
              AND (r.is_public = true OR r.host_id = auth.uid() OR is_room_member(r.id, auth.uid()))
        )
    );

DROP POLICY IF EXISTS "room_queue_votes: update own vote" ON public.room_queue_votes;
CREATE POLICY "room_queue_votes: update own vote" ON public.room_queue_votes
    FOR UPDATE TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS "room_queue_votes: delete own vote" ON public.room_queue_votes;
CREATE POLICY "room_queue_votes: delete own vote" ON public.room_queue_votes
    FOR DELETE TO authenticated
    USING (user_id = auth.uid());

-- Room Sync Audit Log Policies
DROP POLICY IF EXISTS "room_sync_audit_log: select room members" ON public.room_sync_audit_log;
CREATE POLICY "room_sync_audit_log: select room members" ON public.room_sync_audit_log
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.rooms r
            WHERE r.id = room_sync_audit_log.room_id
              AND (r.is_public = true OR r.host_id = auth.uid() OR is_room_member(r.id, auth.uid()))
        )
    );

COMMIT;
