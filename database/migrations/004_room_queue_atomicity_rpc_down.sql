-- Rollback Migration: 004_room_queue_atomicity_rpc_down.sql
-- Description: Drop atomic queue dual-write RPC functions

BEGIN;

DROP FUNCTION IF EXISTS public.next_room_track_atomic(uuid, uuid, bigint, bigint);
DROP FUNCTION IF EXISTS public.remove_room_queue_item_atomic(uuid, uuid, uuid, bigint);
DROP FUNCTION IF EXISTS public.vote_room_queue_item_atomic(uuid, uuid, uuid, int, bigint);
DROP FUNCTION IF EXISTS public.add_room_queue_item_atomic(uuid, uuid, uuid, bigint);

COMMIT;
