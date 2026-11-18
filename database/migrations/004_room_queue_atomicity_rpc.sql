-- Migration: 004_room_queue_atomicity_rpc.sql
-- Description: Transactional PostgreSQL RPC functions for atomic dual-write queue operations
-- Note: Do NOT execute or apply automatically.

BEGIN;

-- 1. Atomic Queue Add & Dual-Write
CREATE OR REPLACE FUNCTION public.add_room_queue_item_atomic(
    p_room_id uuid,
    p_track_id uuid,
    p_user_id uuid,
    p_expected_revision bigint DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_current_rev bigint;
    v_next_pos int;
    v_item_id uuid;
    v_now timestamptz := clock_timestamp();
    v_now_iso text := to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
    v_queue jsonb;
    v_room_is_active boolean;
BEGIN
    -- 1. Lock room row for update to ensure monotonic serialization
    SELECT revision, is_active INTO v_current_rev, v_room_is_active
    FROM public.rooms
    WHERE id = p_room_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Room not found' USING ERRCODE = 'P0002';
    END IF;

    IF NOT v_room_is_active THEN
        RAISE EXCEPTION 'Room is inactive' USING ERRCODE = 'P0003';
    END IF;

    -- 2. Concurrency check
    IF p_expected_revision IS NOT NULL AND v_current_rev != p_expected_revision THEN
        RAISE EXCEPTION 'Revision conflict: expected %, current %', p_expected_revision, v_current_rev USING ERRCODE = 'P0004';
    END IF;

    -- 3. Calculate next queue position
    SELECT COALESCE(MAX(position), -1) + 1 INTO v_next_pos
    FROM public.room_queue
    WHERE room_id = p_room_id AND played_at IS NULL;

    -- 4. Insert normalized queue item
    INSERT INTO public.room_queue (
        room_id, track_id, added_by, position, vote_count, played_at, created_at
    ) VALUES (
        p_room_id, p_track_id, p_user_id, v_next_pos, 0, NULL, v_now
    ) RETURNING id INTO v_item_id;

    -- 5. Reconstruct ordered legacy queue
    SELECT COALESCE(jsonb_agg(q.track_id ORDER BY q.vote_count DESC, q.position ASC), '[]'::jsonb)
    INTO v_queue
    FROM public.room_queue q
    WHERE q.room_id = p_room_id AND q.played_at IS NULL;

    -- 6. Update rooms record atomically
    UPDATE public.rooms
    SET
        queue = v_queue,
        revision = v_current_rev + 1,
        server_timestamp = v_now,
        updated_by = p_user_id,
        updated_at = v_now
    WHERE id = p_room_id;

    -- 7. Audit log
    INSERT INTO public.room_sync_audit_log (
        room_id, user_id, action, revision, payload, created_at
    ) VALUES (
        p_room_id, p_user_id, 'QUEUE_ADD', v_current_rev + 1,
        jsonb_build_object('track_id', p_track_id, 'queue_item_id', v_item_id),
        v_now
    );

    RETURN jsonb_build_object(
        'queue_item_id', v_item_id,
        'position', v_next_pos,
        'revision', v_current_rev + 1,
        'queue', v_queue,
        'server_timestamp', v_now_iso
    );
END;
$$;

REVOKE ALL ON FUNCTION public.add_room_queue_item_atomic(uuid, uuid, uuid, bigint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.add_room_queue_item_atomic(uuid, uuid, uuid, bigint) TO service_role;


-- 2. Atomic Queue Vote & Dual-Write Dynamic Re-sort
CREATE OR REPLACE FUNCTION public.vote_room_queue_item_atomic(
    p_room_id uuid,
    p_queue_item_id uuid,
    p_user_id uuid,
    p_vote int,
    p_expected_revision bigint DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_item_room_id uuid;
    v_item_played_at timestamptz;
    v_current_rev bigint;
    v_total_votes int;
    v_now timestamptz := clock_timestamp();
    v_now_iso text := to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
    v_queue jsonb;
    v_room_is_active boolean;
BEGIN
    -- 1. Validate vote value
    IF p_vote NOT IN (1, -1) THEN
        RAISE EXCEPTION 'Vote must be 1 or -1' USING ERRCODE = 'P0005';
    END IF;

    -- 2. Verify queue item exists, belongs to requested room, and is unplayed
    SELECT room_id, played_at INTO v_item_room_id, v_item_played_at
    FROM public.room_queue
    WHERE id = p_queue_item_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue item not found' USING ERRCODE = 'P0002';
    END IF;

    IF v_item_room_id != p_room_id THEN
        RAISE EXCEPTION 'Queue item does not belong to this room' USING ERRCODE = '42501';
    END IF;

    IF v_item_played_at IS NOT NULL THEN
        RAISE EXCEPTION 'Cannot vote on played queue item' USING ERRCODE = 'P0006';
    END IF;

    -- 3. Lock room for update
    SELECT revision, is_active INTO v_current_rev, v_room_is_active
    FROM public.rooms
    WHERE id = p_room_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Room not found' USING ERRCODE = 'P0002';
    END IF;

    IF NOT v_room_is_active THEN
        RAISE EXCEPTION 'Room is inactive' USING ERRCODE = 'P0003';
    END IF;

    IF p_expected_revision IS NOT NULL AND v_current_rev != p_expected_revision THEN
        RAISE EXCEPTION 'Revision conflict: expected %, current %', p_expected_revision, v_current_rev USING ERRCODE = 'P0004';
    END IF;

    -- 4. Upsert vote
    INSERT INTO public.room_queue_votes (
        queue_item_id, user_id, vote, created_at
    ) VALUES (
        p_queue_item_id, p_user_id, p_vote, v_now
    ) ON CONFLICT (queue_item_id, user_id)
    DO UPDATE SET vote = EXCLUDED.vote, created_at = EXCLUDED.created_at;

    -- 5. Recalculate vote count on item
    SELECT COALESCE(SUM(vote), 0) INTO v_total_votes
    FROM public.room_queue_votes
    WHERE queue_item_id = p_queue_item_id;

    UPDATE public.room_queue
    SET vote_count = v_total_votes
    WHERE id = p_queue_item_id;

    -- 6. Reconstruct ordered legacy queue
    SELECT COALESCE(jsonb_agg(q.track_id ORDER BY q.vote_count DESC, q.position ASC), '[]'::jsonb)
    INTO v_queue
    FROM public.room_queue q
    WHERE q.room_id = p_room_id AND q.played_at IS NULL;

    -- 7. Update room record
    UPDATE public.rooms
    SET
        queue = v_queue,
        revision = v_current_rev + 1,
        server_timestamp = v_now,
        updated_by = p_user_id,
        updated_at = v_now
    WHERE id = p_room_id;

    -- 8. Audit log
    INSERT INTO public.room_sync_audit_log (
        room_id, user_id, action, revision, payload, created_at
    ) VALUES (
        p_room_id, p_user_id, 'QUEUE_VOTE', v_current_rev + 1,
        jsonb_build_object('queue_item_id', p_queue_item_id, 'vote', p_vote, 'vote_count', v_total_votes),
        v_now
    );

    RETURN jsonb_build_object(
        'queue_item_id', p_queue_item_id,
        'vote_count', v_total_votes,
        'revision', v_current_rev + 1,
        'queue', v_queue,
        'server_timestamp', v_now_iso
    );
END;
$$;

REVOKE ALL ON FUNCTION public.vote_room_queue_item_atomic(uuid, uuid, uuid, int, bigint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.vote_room_queue_item_atomic(uuid, uuid, uuid, int, bigint) TO service_role;


-- 3. Atomic Queue Remove & Dual-Write
CREATE OR REPLACE FUNCTION public.remove_room_queue_item_atomic(
    p_room_id uuid,
    p_queue_item_id uuid,
    p_user_id uuid,
    p_expected_revision bigint DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_item_room_id uuid;
    v_current_rev bigint;
    v_now timestamptz := clock_timestamp();
    v_now_iso text := to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
    v_queue jsonb;
    v_room_is_active boolean;
BEGIN
    SELECT room_id INTO v_item_room_id
    FROM public.room_queue
    WHERE id = p_queue_item_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue item not found' USING ERRCODE = 'P0002';
    END IF;

    IF v_item_room_id != p_room_id THEN
        RAISE EXCEPTION 'Queue item does not belong to this room' USING ERRCODE = '42501';
    END IF;

    SELECT revision, is_active INTO v_current_rev, v_room_is_active
    FROM public.rooms
    WHERE id = p_room_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Room not found' USING ERRCODE = 'P0002';
    END IF;

    IF NOT v_room_is_active THEN
        RAISE EXCEPTION 'Room is inactive' USING ERRCODE = 'P0003';
    END IF;

    IF p_expected_revision IS NOT NULL AND v_current_rev != p_expected_revision THEN
        RAISE EXCEPTION 'Revision conflict: expected %, current %', p_expected_revision, v_current_rev USING ERRCODE = 'P0004';
    END IF;

    DELETE FROM public.room_queue WHERE id = p_queue_item_id AND room_id = p_room_id;

    SELECT COALESCE(jsonb_agg(q.track_id ORDER BY q.vote_count DESC, q.position ASC), '[]'::jsonb)
    INTO v_queue
    FROM public.room_queue q
    WHERE q.room_id = p_room_id AND q.played_at IS NULL;

    UPDATE public.rooms
    SET
        queue = v_queue,
        revision = v_current_rev + 1,
        server_timestamp = v_now,
        updated_by = p_user_id,
        updated_at = v_now
    WHERE id = p_room_id;

    INSERT INTO public.room_sync_audit_log (
        room_id, user_id, action, revision, payload, created_at
    ) VALUES (
        p_room_id, p_user_id, 'QUEUE_REMOVE', v_current_rev + 1,
        jsonb_build_object('queue_item_id', p_queue_item_id),
        v_now
    );

    RETURN jsonb_build_object(
        'queue_item_id', p_queue_item_id,
        'revision', v_current_rev + 1,
        'queue', v_queue,
        'server_timestamp', v_now_iso
    );
END;
$$;

REVOKE ALL ON FUNCTION public.remove_room_queue_item_atomic(uuid, uuid, uuid, bigint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.remove_room_queue_item_atomic(uuid, uuid, uuid, bigint) TO service_role;


-- 4. Atomic Next Track Playback & Queue Advance
CREATE OR REPLACE FUNCTION public.next_room_track_atomic(
    p_room_id uuid,
    p_user_id uuid,
    p_scheduled_delay_ms bigint DEFAULT 800,
    p_expected_revision bigint DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_current_rev bigint;
    v_room_is_active boolean;
    v_next_item_id uuid;
    v_next_track_id uuid;
    v_now timestamptz := clock_timestamp();
    v_now_iso text := to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
    v_now_ms bigint := (EXTRACT(EPOCH FROM v_now) * 1000)::bigint;
    v_delay_ms bigint;
    v_scheduled_start_ms bigint;
    v_queue jsonb;
    v_has_next boolean := false;
BEGIN
    -- 1. Lock room row for update
    SELECT revision, is_active INTO v_current_rev, v_room_is_active
    FROM public.rooms
    WHERE id = p_room_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Room not found' USING ERRCODE = 'P0002';
    END IF;

    IF NOT v_room_is_active THEN
        RAISE EXCEPTION 'Room is inactive' USING ERRCODE = 'P0003';
    END IF;

    -- 2. Concurrency check
    IF p_expected_revision IS NOT NULL AND v_current_rev != p_expected_revision THEN
        RAISE EXCEPTION 'Revision conflict: expected %, current %', p_expected_revision, v_current_rev USING ERRCODE = 'P0004';
    END IF;

    -- 3. Bounds check delay (between 500ms and 1500ms)
    v_delay_ms := GREATEST(500, LEAST(1500, COALESCE(p_scheduled_delay_ms, 800)));
    v_scheduled_start_ms := v_now_ms + v_delay_ms;

    -- 4. Select and lock next unplayed queue item
    SELECT id, track_id INTO v_next_item_id, v_next_track_id
    FROM public.room_queue
    WHERE room_id = p_room_id AND played_at IS NULL
    ORDER BY vote_count DESC, position ASC
    LIMIT 1
    FOR UPDATE;

    IF FOUND THEN
        v_has_next := true;

        -- Mark item played
        UPDATE public.room_queue
        SET played_at = v_now
        WHERE id = v_next_item_id;

        -- Reconstruct remaining legacy queue
        SELECT COALESCE(jsonb_agg(q.track_id ORDER BY q.vote_count DESC, q.position ASC), '[]'::jsonb)
        INTO v_queue
        FROM public.room_queue q
        WHERE q.room_id = p_room_id AND q.played_at IS NULL;

        -- Update room playback state atomically
        UPDATE public.rooms
        SET
            current_track_id = v_next_track_id,
            playback_position_ms = 0,
            is_playing = true,
            playback_updated_at = v_now,
            scheduled_start_ms = v_scheduled_start_ms,
            queue = v_queue,
            revision = v_current_rev + 1,
            server_timestamp = v_now,
            updated_by = p_user_id,
            updated_at = v_now
        WHERE id = p_room_id;
    ELSE
        -- No remaining tracks in queue
        SELECT COALESCE(jsonb_agg(q.track_id ORDER BY q.vote_count DESC, q.position ASC), '[]'::jsonb)
        INTO v_queue
        FROM public.room_queue q
        WHERE q.room_id = p_room_id AND q.played_at IS NULL;

        UPDATE public.rooms
        SET
            revision = v_current_rev + 1,
            server_timestamp = v_now,
            updated_by = p_user_id,
            updated_at = v_now
        WHERE id = p_room_id;
    END IF;

    -- 5. Audit log
    INSERT INTO public.room_sync_audit_log (
        room_id, user_id, action, revision, payload, created_at
    ) VALUES (
        p_room_id, p_user_id, 'NEXT_TRACK', v_current_rev + 1,
        jsonb_build_object('next_track_id', v_next_track_id, 'played_item_id', v_next_item_id, 'has_next', v_has_next),
        v_now
    );

    RETURN jsonb_build_object(
        'has_next', v_has_next,
        'current_track_id', v_next_track_id,
        'played_item_id', v_next_item_id,
        'is_playing', v_has_next,
        'scheduled_start_ms', CASE WHEN v_has_next THEN v_scheduled_start_ms ELSE NULL END,
        'revision', v_current_rev + 1,
        'queue', v_queue,
        'server_timestamp', v_now_iso
    );
END;
$$;

REVOKE ALL ON FUNCTION public.next_room_track_atomic(uuid, uuid, bigint, bigint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.next_room_track_atomic(uuid, uuid, bigint, bigint) TO service_role;

COMMIT;
