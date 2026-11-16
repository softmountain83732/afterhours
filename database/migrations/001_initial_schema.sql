-- ============================================================================
-- Afterhours – Initial Schema Migration
-- ============================================================================

-- =========================  EXTENSIONS  ====================================
create extension if not exists "pgcrypto";

-- =========================  TRIGGER HELPERS  ===============================

-- trigger helper: auto-set updated_at
create or replace function public.handle_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ============================================================================
-- 1. PROFILES
-- ============================================================================
create table public.profiles (
  id             uuid        primary key references auth.users on delete cascade,
  telegram_id    bigint      unique not null,
  username       text        unique not null,
  display_name   text        not null,
  bio            text,
  avatar_url     text,
  gender         text,
  gender_public  boolean     not null default false,
  is_guest       boolean     not null default false,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create trigger profiles_updated_at
  before update on public.profiles
  for each row execute function public.handle_updated_at();

-- ============================================================================
-- 2. ARTISTS
-- ============================================================================
create table public.artists (
  id         uuid        primary key default gen_random_uuid(),
  name       text        not null,
  image_url  text,
  created_at timestamptz not null default now()
);

create unique index artists_name_lower_idx on public.artists (lower(name));

-- ============================================================================
-- 3. ALBUMS
-- ============================================================================
create table public.albums (
  id               uuid        primary key default gen_random_uuid(),
  title            text        not null,
  artist_id        uuid        references public.artists on delete set null,
  artwork_url      text,
  artwork_thumb_url text,
  release_year     int,
  created_at       timestamptz not null default now()
);

create index albums_artist_id_idx on public.albums (artist_id);

-- ============================================================================
-- 4. TRACKS
-- ============================================================================
create table public.tracks (
  id                  uuid        primary key default gen_random_uuid(),
  title               text        not null,
  artist_id           uuid        references public.artists on delete set null,
  album_id            uuid        references public.albums  on delete set null,
  duration_ms         int,
  track_number        int,
  codec               text,
  bitrate             int,
  file_hash           text        unique,
  telegram_chat_id    bigint,
  telegram_message_id bigint,
  uploaded_by         uuid        references public.profiles on delete set null,
  is_private          boolean     not null default false,
  file_size           bigint,
  metadata_raw        jsonb,
  ingestion_status    text        not null default 'pending',
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create index tracks_file_hash_idx   on public.tracks (file_hash);
create index tracks_artist_id_idx   on public.tracks (artist_id);
create index tracks_album_id_idx    on public.tracks (album_id);
create index tracks_uploaded_by_idx on public.tracks (uploaded_by);

create trigger tracks_updated_at
  before update on public.tracks
  for each row execute function public.handle_updated_at();

-- ============================================================================
-- 5. TRACK_ARTWORK
-- ============================================================================
create table public.track_artwork (
  id           uuid        primary key default gen_random_uuid(),
  track_id     uuid        not null references public.tracks on delete cascade,
  image_hash   text        unique,
  original_url text,
  thumb_url    text,
  created_at   timestamptz not null default now()
);

create index track_artwork_track_id_idx on public.track_artwork (track_id);

-- ============================================================================
-- 6. FAVORITES
-- ============================================================================
create table public.favorites (
  id         uuid        primary key default gen_random_uuid(),
  user_id    uuid        not null references public.profiles on delete cascade,
  track_id   uuid        not null references public.tracks  on delete cascade,
  created_at timestamptz not null default now(),

  unique (user_id, track_id)
);

create index favorites_user_id_idx  on public.favorites (user_id);
create index favorites_track_id_idx on public.favorites (track_id);

-- ============================================================================
-- 7. PLAYLISTS
-- ============================================================================
create table public.playlists (
  id          uuid        primary key default gen_random_uuid(),
  user_id     uuid        not null references public.profiles on delete cascade,
  name        text        not null,
  description text,
  is_public   boolean     not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index playlists_user_id_idx on public.playlists (user_id);

create trigger playlists_updated_at
  before update on public.playlists
  for each row execute function public.handle_updated_at();

-- ============================================================================
-- 8. PLAYLIST_TRACKS
-- ============================================================================
create table public.playlist_tracks (
  id          uuid        primary key default gen_random_uuid(),
  playlist_id uuid        not null references public.playlists on delete cascade,
  track_id    uuid        not null references public.tracks    on delete cascade,
  position    int         not null,
  added_at    timestamptz not null default now(),

  unique (playlist_id, track_id)
);

create index playlist_tracks_playlist_id_idx on public.playlist_tracks (playlist_id);

-- ============================================================================
-- 9. LISTENING_HISTORY
-- ============================================================================
create table public.listening_history (
  id                   uuid        primary key default gen_random_uuid(),
  user_id              uuid        not null references public.profiles on delete cascade,
  track_id             uuid        not null references public.tracks   on delete cascade,
  played_at            timestamptz not null default now(),
  duration_listened_ms int
);

create index listening_history_user_id_idx    on public.listening_history (user_id);
create index listening_history_played_at_idx  on public.listening_history (user_id, played_at desc);

-- ============================================================================
-- 10. ROOMS
-- ============================================================================
create table public.rooms (
  id                   uuid        primary key default gen_random_uuid(),
  name                 text        not null,
  host_id              uuid        not null references public.profiles on delete cascade,
  is_public            boolean     not null default true,
  is_active            boolean     not null default true,
  invite_token         text        unique,
  max_members          int         not null default 30,
  current_track_id     uuid        references public.tracks on delete set null,
  playback_position_ms int         not null default 0,
  is_playing           boolean     not null default false,
  playback_updated_at  timestamptz,
  queue                jsonb       not null default '[]'::jsonb,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

create index rooms_host_id_idx    on public.rooms (host_id);
create index rooms_is_active_idx  on public.rooms (is_active) where is_active = true;

create trigger rooms_updated_at
  before update on public.rooms
  for each row execute function public.handle_updated_at();

-- ============================================================================
-- 11. ROOM_MEMBERS
-- ============================================================================
create table public.room_members (
  id                    uuid        primary key default gen_random_uuid(),
  room_id               uuid        not null references public.rooms    on delete cascade,
  user_id               uuid        not null references public.profiles on delete cascade,
  can_control_playback  boolean     not null default false,
  can_manage_queue      boolean     not null default false,
  joined_at             timestamptz not null default now(),

  unique (room_id, user_id)
);

create index room_members_room_id_idx on public.room_members (room_id);
create index room_members_user_id_idx on public.room_members (user_id);

-- ----------------------------------------------------------------------------
-- ROOM HELPER FUNCTIONS
-- Defined here so public.rooms and public.room_members exist before definition
-- ----------------------------------------------------------------------------
create or replace function is_room_member(room_uuid uuid, user_uuid uuid)
returns boolean
language sql
stable
security definer
as $$
  select exists (
    select 1 from public.room_members
    where room_id = room_uuid and user_id = user_uuid
  );
$$;

create or replace function is_room_host(room_uuid uuid, user_uuid uuid)
returns boolean
language sql
stable
security definer
as $$
  select exists (
    select 1 from public.rooms
    where id = room_uuid and host_id = user_uuid
  );
$$;

-- ============================================================================
-- 12. ROOM_MESSAGES
-- ============================================================================
create table public.room_messages (
  id         uuid        primary key default gen_random_uuid(),
  room_id    uuid        not null references public.rooms    on delete cascade,
  user_id    uuid        not null references public.profiles on delete cascade,
  content    text        not null check (char_length(content) <= 1000),
  created_at timestamptz not null default now()
);

create index room_messages_room_id_idx on public.room_messages (room_id, created_at);

-- ============================================================================
-- 13. TRACK_ACCESS
-- ============================================================================
create table public.track_access (
  id         uuid        primary key default gen_random_uuid(),
  track_id   uuid        not null references public.tracks   on delete cascade,
  owner_id   uuid        not null references public.profiles on delete cascade,
  room_id    uuid        not null references public.rooms    on delete cascade,
  granted_at timestamptz not null default now(),
  expires_at timestamptz
);

create index track_access_track_id_idx on public.track_access (track_id);
create index track_access_room_id_idx  on public.track_access (room_id);

-- ============================================================================
-- 14. REPORTS
-- ============================================================================
create table public.reports (
  id               uuid        primary key default gen_random_uuid(),
  reporter_id      uuid        not null references public.profiles on delete cascade,
  reported_user_id uuid        not null references public.profiles on delete cascade,
  room_id          uuid        references public.rooms on delete set null,
  reason           text        not null,
  status           text        not null default 'pending',
  created_at       timestamptz not null default now()
);

create index reports_reporter_id_idx on public.reports (reporter_id);

-- ============================================================================
-- 15. USER_BLOCKS
-- ============================================================================
create table public.user_blocks (
  id         uuid        primary key default gen_random_uuid(),
  blocker_id uuid        not null references public.profiles on delete cascade,
  blocked_id uuid        not null references public.profiles on delete cascade,
  created_at timestamptz not null default now(),

  unique (blocker_id, blocked_id)
);

create index user_blocks_blocker_id_idx on public.user_blocks (blocker_id);
create index user_blocks_blocked_id_idx on public.user_blocks (blocked_id);

-- ============================================================================
-- 16. UPLOAD_JOBS
-- ============================================================================
create table public.upload_jobs (
  id                  uuid        primary key default gen_random_uuid(),
  telegram_message_id bigint,
  telegram_chat_id    bigint,
  status              text        not null default 'pending',
  error_message       text,
  attempts            int         not null default 0,
  uploaded_by         uuid        references public.profiles on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create index upload_jobs_uploaded_by_idx on public.upload_jobs (uploaded_by);

create trigger upload_jobs_updated_at
  before update on public.upload_jobs
  for each row execute function public.handle_updated_at();

-- ============================================================================
-- ROW-LEVEL SECURITY
-- ============================================================================

-- -----------  profiles  -----------------------------------------------------
alter table public.profiles enable row level security;

create policy "profiles: anyone authenticated can read"
  on public.profiles for select
  to authenticated
  using (true);

create policy "profiles: update own only"
  on public.profiles for update
  to authenticated
  using (id = auth.uid())
  with check (id = auth.uid());

-- -----------  artists  ------------------------------------------------------
alter table public.artists enable row level security;

create policy "artists: anyone authenticated can read"
  on public.artists for select
  to authenticated
  using (true);

create policy "artists: authenticated can insert"
  on public.artists for insert
  to authenticated
  with check (true);

-- -----------  albums  -------------------------------------------------------
alter table public.albums enable row level security;

create policy "albums: anyone authenticated can read"
  on public.albums for select
  to authenticated
  using (true);

create policy "albums: authenticated can insert"
  on public.albums for insert
  to authenticated
  with check (true);

-- -----------  tracks  -------------------------------------------------------
alter table public.tracks enable row level security;

create policy "tracks: select non-private or own"
  on public.tracks for select
  to authenticated
  using (
    is_private = false
    or uploaded_by = auth.uid()
    or exists (
      select 1 from public.track_access ta
      where ta.track_id = id
        and exists (
          select 1 from public.room_members rm
          where rm.room_id = ta.room_id
            and rm.user_id = auth.uid()
        )
        and (ta.expires_at is null or ta.expires_at > now())
    )
  );

create policy "tracks: authenticated can insert"
  on public.tracks for insert
  to authenticated
  with check (true);

create policy "tracks: update own only"
  on public.tracks for update
  to authenticated
  using (uploaded_by = auth.uid())
  with check (uploaded_by = auth.uid());

-- -----------  track_artwork  ------------------------------------------------
alter table public.track_artwork enable row level security;

create policy "track_artwork: anyone authenticated can read"
  on public.track_artwork for select
  to authenticated
  using (true);

create policy "track_artwork: authenticated can insert"
  on public.track_artwork for insert
  to authenticated
  with check (true);

-- -----------  favorites  ----------------------------------------------------
alter table public.favorites enable row level security;

create policy "favorites: select own"
  on public.favorites for select
  to authenticated
  using (user_id = auth.uid());

create policy "favorites: insert own"
  on public.favorites for insert
  to authenticated
  with check (user_id = auth.uid());

create policy "favorites: delete own"
  on public.favorites for delete
  to authenticated
  using (user_id = auth.uid());

-- -----------  playlists  ----------------------------------------------------
alter table public.playlists enable row level security;

create policy "playlists: select own or public"
  on public.playlists for select
  to authenticated
  using (user_id = auth.uid() or is_public = true);

create policy "playlists: insert own"
  on public.playlists for insert
  to authenticated
  with check (user_id = auth.uid());

create policy "playlists: update own"
  on public.playlists for update
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy "playlists: delete own"
  on public.playlists for delete
  to authenticated
  using (user_id = auth.uid());

-- -----------  playlist_tracks  ----------------------------------------------
alter table public.playlist_tracks enable row level security;

create policy "playlist_tracks: select if can see playlist"
  on public.playlist_tracks for select
  to authenticated
  using (
    exists (
      select 1 from public.playlists p
      where p.id = playlist_id
        and (p.user_id = auth.uid() or p.is_public = true)
    )
  );

create policy "playlist_tracks: insert own playlists"
  on public.playlist_tracks for insert
  to authenticated
  with check (
    exists (
      select 1 from public.playlists p
      where p.id = playlist_id
        and p.user_id = auth.uid()
    )
  );

create policy "playlist_tracks: update own playlists"
  on public.playlist_tracks for update
  to authenticated
  using (
    exists (
      select 1 from public.playlists p
      where p.id = playlist_id
        and p.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from public.playlists p
      where p.id = playlist_id
        and p.user_id = auth.uid()
    )
  );

create policy "playlist_tracks: delete own playlists"
  on public.playlist_tracks for delete
  to authenticated
  using (
    exists (
      select 1 from public.playlists p
      where p.id = playlist_id
        and p.user_id = auth.uid()
    )
  );

-- -----------  listening_history  --------------------------------------------
alter table public.listening_history enable row level security;

create policy "listening_history: select own"
  on public.listening_history for select
  to authenticated
  using (user_id = auth.uid());

create policy "listening_history: insert own"
  on public.listening_history for insert
  to authenticated
  with check (user_id = auth.uid());

create policy "listening_history: delete own"
  on public.listening_history for delete
  to authenticated
  using (user_id = auth.uid());

-- -----------  rooms  --------------------------------------------------------
alter table public.rooms enable row level security;

create policy "rooms: select public or member"
  on public.rooms for select
  to authenticated
  using (
    is_public = true
    or host_id = auth.uid()
    or is_room_member(id, auth.uid())
  );

create policy "rooms: insert authenticated"
  on public.rooms for insert
  to authenticated
  with check (host_id = auth.uid());

create policy "rooms: update host only"
  on public.rooms for update
  to authenticated
  using (host_id = auth.uid())
  with check (host_id = auth.uid());

-- -----------  room_members  -------------------------------------------------
alter table public.room_members enable row level security;

create policy "room_members: select if in same room"
  on public.room_members for select
  to authenticated
  using (is_room_member(room_id, auth.uid()));

create policy "room_members: insert (join room)"
  on public.room_members for insert
  to authenticated
  with check (
    user_id = auth.uid()
    and (
      exists (
        select 1 from public.rooms r
        where r.id = room_id
          and r.is_active = true
          and (r.is_public = true or r.host_id = auth.uid())
      )
    )
  );

create policy "room_members: delete (leave or host kick)"
  on public.room_members for delete
  to authenticated
  using (
    user_id = auth.uid()
    or is_room_host(room_id, auth.uid())
  );

-- -----------  room_messages  ------------------------------------------------
alter table public.room_messages enable row level security;

create policy "room_messages: select if room member"
  on public.room_messages for select
  to authenticated
  using (is_room_member(room_id, auth.uid()));

create policy "room_messages: insert if room member"
  on public.room_messages for insert
  to authenticated
  with check (
    user_id = auth.uid()
    and is_room_member(room_id, auth.uid())
  );

-- -----------  track_access  -------------------------------------------------
alter table public.track_access enable row level security;

create policy "track_access: select if owner or in room"
  on public.track_access for select
  to authenticated
  using (
    owner_id = auth.uid()
    or is_room_member(room_id, auth.uid())
  );

create policy "track_access: insert if track owner"
  on public.track_access for insert
  to authenticated
  with check (
    owner_id = auth.uid()
    and exists (
      select 1 from public.tracks t
      where t.id = track_id
        and t.uploaded_by = auth.uid()
    )
  );

-- -----------  reports  ------------------------------------------------------
alter table public.reports enable row level security;

create policy "reports: insert authenticated"
  on public.reports for insert
  to authenticated
  with check (reporter_id = auth.uid());

create policy "reports: select own"
  on public.reports for select
  to authenticated
  using (reporter_id = auth.uid());

-- -----------  user_blocks  --------------------------------------------------
alter table public.user_blocks enable row level security;

create policy "user_blocks: select own"
  on public.user_blocks for select
  to authenticated
  using (blocker_id = auth.uid());

create policy "user_blocks: insert own"
  on public.user_blocks for insert
  to authenticated
  with check (blocker_id = auth.uid());

create policy "user_blocks: delete own"
  on public.user_blocks for delete
  to authenticated
  using (blocker_id = auth.uid());

-- -----------  upload_jobs  --------------------------------------------------
alter table public.upload_jobs enable row level security;

create policy "upload_jobs: select own"
  on public.upload_jobs for select
  to authenticated
  using (uploaded_by = auth.uid());

-- ============================================================================
-- END OF MIGRATION
-- ============================================================================
