"""SQLite persistence."""
import secrets
import sqlite3
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS station (
 id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, description TEXT NOT NULL,
 callsign TEXT NOT NULL UNIQUE, registration_open INTEGER NOT NULL DEFAULT 1,
 registration_mode TEXT NOT NULL DEFAULT 'open'
 CHECK(registration_mode IN ('open','private','disabled')),
 guest_access INTEGER NOT NULL DEFAULT 1, landing_message TEXT NOT NULL DEFAULT '',
 landing_heading TEXT NOT NULL DEFAULT '',
 landing_message_enabled INTEGER NOT NULL DEFAULT 1
 CHECK(landing_message_enabled IN (0,1)),
 brand_tagline TEXT NOT NULL DEFAULT 'H7/dS! // YOUR INDEPENDENT COMMUNITY STATION //',
 header_font TEXT NOT NULL DEFAULT 'small',
 header_font_size INTEGER NOT NULL DEFAULT 100,
 header_text TEXT NOT NULL DEFAULT '',
 header_color TEXT NOT NULL DEFAULT '',
 upload_permission TEXT NOT NULL DEFAULT 'members',
 file_storage_limit_mb INTEGER NOT NULL DEFAULT 100,
 file_size_limit_mb INTEGER NOT NULL DEFAULT 10,
 radio_default_quota_mb INTEGER NOT NULL DEFAULT 100,
 radio_broadcast_limit_minutes INTEGER NOT NULL DEFAULT 120,
 mud_enabled INTEGER NOT NULL DEFAULT 0 CHECK(mud_enabled IN (0,1)),
 mud_traditional_enabled INTEGER NOT NULL DEFAULT 0 CHECK(mud_traditional_enabled IN (0,1)),
 mud_procedural_enabled INTEGER NOT NULL DEFAULT 0 CHECK(mud_procedural_enabled IN (0,1)),
 user_feeds_enabled INTEGER NOT NULL DEFAULT 1 CHECK(user_feeds_enabled IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
 callsign TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL,
 is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 tagline TEXT NOT NULL DEFAULT '', about TEXT NOT NULL DEFAULT '', birthday TEXT,
 gender TEXT NOT NULL DEFAULT '', last_seen TEXT, connection_id TEXT,
 radio_quota_mb INTEGER, profile_photo_filename TEXT);
CREATE TABLE IF NOT EXISTS registration_invites (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 token_hash TEXT NOT NULL UNIQUE,
 code_hint TEXT NOT NULL,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 used_by INTEGER UNIQUE REFERENCES users(id) ON DELETE SET NULL,
 used_at TEXT,
 revoked_at TEXT,
 CHECK((used_at IS NULL AND used_by IS NULL) OR used_at IS NOT NULL));
CREATE INDEX IF NOT EXISTS idx_registration_invites_status
 ON registration_invites(revoked_at,used_at,created_at);
CREATE TABLE IF NOT EXISTS posts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 parent_id INTEGER REFERENCES posts(id) ON DELETE CASCADE, title TEXT, body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((parent_id IS NULL AND title IS NOT NULL) OR parent_id IS NOT NULL));
CREATE INDEX IF NOT EXISTS idx_posts_parent ON posts(parent_id);
CREATE TABLE IF NOT EXISTS user_feed_posts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 200),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_user_feed_posts_owner ON user_feed_posts(owner_id,id DESC);
CREATE TABLE IF NOT EXISTS user_feed_comments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 post_id INTEGER NOT NULL REFERENCES user_feed_posts(id) ON DELETE CASCADE,
 author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 500),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_user_feed_comments_post ON user_feed_comments(post_id,id);
CREATE TABLE IF NOT EXISTS user_feed_reactions (
 post_id INTEGER NOT NULL REFERENCES user_feed_posts(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('like','heart','laugh')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(post_id,user_id));
CREATE TABLE IF NOT EXISTS user_feed_reaction_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_user_feed_reaction_events_user
 ON user_feed_reaction_events(user_id,created_at);
CREATE TABLE IF NOT EXISTS station_chat_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 body TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_station_chat_created ON station_chat_messages(id);
CREATE TABLE IF NOT EXISTS moderation_restrictions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
 kind TEXT NOT NULL CHECK(kind IN ('suspension','ban','mute')),
 starts_at TEXT NOT NULL, expires_at TEXT, ended_at TEXT,
 actor_id INTEGER REFERENCES users(id), reason TEXT NOT NULL, private_note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_moderation_restrictions_user ON moderation_restrictions(user_id,kind,ended_at,expires_at);
CREATE TABLE IF NOT EXISTS moderation_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER REFERENCES users(id),
 user_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
 target_type TEXT, target_id INTEGER, reason TEXT NOT NULL DEFAULT '',
 private_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_moderation_audit_user ON moderation_audit(user_id,id);
CREATE TABLE IF NOT EXISTS moderation_reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER NOT NULL REFERENCES users(id),
 target_type TEXT NOT NULL CHECK(target_type IN ('user','chat','bulletin','file','feed_post','feed_comment')),
 target_id INTEGER NOT NULL, subject_user_id INTEGER REFERENCES users(id),
 reason TEXT NOT NULL, created_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
 resolver_id INTEGER REFERENCES users(id), resolution_note TEXT NOT NULL DEFAULT '', resolved_at TEXT);
CREATE INDEX IF NOT EXISTS idx_moderation_reports_queue ON moderation_reports(status,id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_moderation_reports_open_unique
 ON moderation_reports(reporter_id,target_type,target_id) WHERE status='open';
CREATE TABLE IF NOT EXISTS station_chat_deletions (
 message_id INTEGER PRIMARY KEY, deleted_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mail (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 thread_id INTEGER REFERENCES mail(id) ON DELETE CASCADE,
 reply_to_id INTEGER REFERENCES mail(id) ON DELETE SET NULL,
 subject TEXT NOT NULL, body TEXT NOT NULL,
 is_read INTEGER NOT NULL DEFAULT 0,
 sender_deleted INTEGER NOT NULL DEFAULT 0,
 recipient_deleted INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mail_recipient ON mail(recipient_id,recipient_deleted,created_at);
CREATE INDEX IF NOT EXISTS idx_mail_sender ON mail(sender_id,sender_deleted,created_at);
CREATE TABLE IF NOT EXISTS archive_files (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 uploader_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 display_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
 category TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS announcements (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 title TEXT NOT NULL, body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS radio_bands (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL,
 start_khz INTEGER NOT NULL, end_khz INTEGER NOT NULL,
 supports_cw INTEGER NOT NULL DEFAULT 0 CHECK(supports_cw IN (0,1)),
 supports_sstv INTEGER NOT NULL DEFAULT 0 CHECK(supports_sstv IN (0,1)),
 supports_live_voice INTEGER NOT NULL DEFAULT 0 CHECK(supports_live_voice IN (0,1)),
 supports_hosted_broadcasts INTEGER NOT NULL DEFAULT 0 CHECK(supports_hosted_broadcasts IN (0,1)),
 allow_open_broadcasts INTEGER NOT NULL DEFAULT 0 CHECK(allow_open_broadcasts IN (0,1)),
 tx_permission TEXT NOT NULL DEFAULT 'members'
 CHECK(tx_permission IN ('members','admins','receive')),
 CHECK(start_khz > 0 AND end_khz >= start_khz));
CREATE TABLE IF NOT EXISTS radio_permission_groups (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
 host_broadcasts INTEGER NOT NULL DEFAULT 0 CHECK(host_broadcasts IN (0,1)));
CREATE UNIQUE INDEX IF NOT EXISTS idx_radio_default_group
 ON radio_permission_groups(is_default) WHERE is_default=1;
CREATE TABLE IF NOT EXISTS radio_group_memberships (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES radio_permission_groups(id) ON DELETE CASCADE,
 PRIMARY KEY(user_id,group_id));
CREATE TABLE IF NOT EXISTS radio_permission_rules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 group_id INTEGER NOT NULL REFERENCES radio_permission_groups(id) ON DELETE CASCADE,
 scope_type TEXT NOT NULL CHECK(scope_type IN ('band','range','frequency')),
 band_id INTEGER REFERENCES radio_bands(id) ON DELETE CASCADE,
 start_khz INTEGER, end_khz INTEGER,
 permission TEXT NOT NULL CHECK(permission IN ('FULL','LISTEN','DENIED')),
 CHECK((scope_type='band' AND band_id IS NOT NULL AND start_khz IS NULL AND end_khz IS NULL)
    OR (scope_type IN ('range','frequency') AND band_id IS NULL AND start_khz IS NOT NULL
        AND end_khz IS NOT NULL AND start_khz>0 AND end_khz>=start_khz)));
CREATE INDEX IF NOT EXISTS idx_radio_memberships_user ON radio_group_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_radio_rules_group ON radio_permission_rules(group_id);
CREATE TABLE IF NOT EXISTS radio_audio_files (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 display_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
 media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>0),
 duration_seconds REAL NOT NULL DEFAULT 180 CHECK(duration_seconds>0),
 title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_radio_audio_owner ON radio_audio_files(owner_id,id);
CREATE TABLE IF NOT EXISTS radio_playlists (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL COLLATE NOCASE, loop_enabled INTEGER NOT NULL DEFAULT 0
 CHECK(loop_enabled IN (0,1)), shuffle_enabled INTEGER NOT NULL DEFAULT 0
 CHECK(shuffle_enabled IN (0,1)), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(owner_id,name));
CREATE INDEX IF NOT EXISTS idx_radio_playlists_owner ON radio_playlists(owner_id,id);
CREATE TABLE IF NOT EXISTS radio_playlist_tracks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 playlist_id INTEGER NOT NULL REFERENCES radio_playlists(id) ON DELETE CASCADE,
 audio_file_id INTEGER REFERENCES radio_audio_files(id) ON DELETE SET NULL,
 display_name TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0));
CREATE INDEX IF NOT EXISTS idx_radio_playlist_tracks
 ON radio_playlist_tracks(playlist_id,position,id);
CREATE TABLE IF NOT EXISTS radio_broadcast_assignments (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 frequency_khz INTEGER NOT NULL UNIQUE CHECK(frequency_khz BETWEEN 100 AND 999990),
 broadcast_name TEXT NOT NULL DEFAULT '',
 broadcast_message TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS radio_automated_broadcasts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL,
 message TEXT NOT NULL DEFAULT '',
 frequency_khz INTEGER NOT NULL UNIQUE CHECK(frequency_khz BETWEEN 100 AND 999990),
 playlist_id INTEGER NOT NULL REFERENCES radio_playlists(id) ON DELETE RESTRICT,
 runtime_minutes INTEGER NOT NULL DEFAULT 120 CHECK(runtime_minutes BETWEEN 1 AND 1440),
 delay_minutes INTEGER NOT NULL DEFAULT 0 CHECK(delay_minutes BETWEEN 0 AND 10080),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_radio_automated_playlist
 ON radio_automated_broadcasts(playlist_id);
CREATE TABLE IF NOT EXISTS radio_broadcast_content (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('music','announcement','jingle','morse','sstv')),
 name TEXT NOT NULL COLLATE NOCASE,
 audio_file_id INTEGER REFERENCES radio_audio_files(id) ON DELETE SET NULL,
 text_content TEXT NOT NULL DEFAULT '',
 stored_name TEXT,
 media_type TEXT NOT NULL DEFAULT '',
 size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes>=0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(owner_id,kind,name));
CREATE INDEX IF NOT EXISTS idx_radio_content_owner
 ON radio_broadcast_content(owner_id,kind,name);
CREATE TABLE IF NOT EXISTS radio_programs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL COLLATE NOCASE,
 description TEXT NOT NULL DEFAULT '',
 loop_enabled INTEGER NOT NULL DEFAULT 0 CHECK(loop_enabled IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(owner_id,name));
CREATE INDEX IF NOT EXISTS idx_radio_programs_owner ON radio_programs(owner_id,id);
CREATE TABLE IF NOT EXISTS radio_program_actions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 program_id INTEGER NOT NULL REFERENCES radio_programs(id) ON DELETE CASCADE,
 action_type TEXT NOT NULL CHECK(action_type IN
  ('playlist','resume_playlist','music','announcement','jingle','morse','sstv','live')),
 playlist_id INTEGER REFERENCES radio_playlists(id) ON DELETE SET NULL,
 content_id INTEGER REFERENCES radio_broadcast_content(id) ON DELETE SET NULL,
 label TEXT NOT NULL DEFAULT '', text_content TEXT NOT NULL DEFAULT '',
 duration_seconds REAL NOT NULL DEFAULT 0 CHECK(duration_seconds>=0),
 repeat_count INTEGER NOT NULL DEFAULT 1 CHECK(repeat_count BETWEEN 1 AND 100),
 position INTEGER NOT NULL CHECK(position>=0), settings_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_radio_program_actions
 ON radio_program_actions(program_id,position,id);
CREATE TABLE IF NOT EXISTS radio_automation_rules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
 frequency_khz INTEGER NOT NULL CHECK(frequency_khz BETWEEN 100 AND 999990),
 program_id INTEGER NOT NULL REFERENCES radio_programs(id) ON DELETE RESTRICT,
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 trigger_type TEXT NOT NULL DEFAULT 'time' CHECK(trigger_type IN
  ('time','interval','playlist_complete','track_interval','dj_connect','dj_disconnect')),
 trigger_value TEXT NOT NULL DEFAULT '',
 runtime_minutes INTEGER NOT NULL DEFAULT 120 CHECK(runtime_minutes BETWEEN 1 AND 1440),
 fallback_program_id INTEGER REFERENCES radio_programs(id) ON DELETE SET NULL,
 last_run_at TEXT, next_run_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(frequency_khz,name));
CREATE INDEX IF NOT EXISTS idx_radio_automation_due
 ON radio_automation_rules(enabled,next_run_at);
CREATE TABLE IF NOT EXISTS application_storage (
 application_slug TEXT NOT NULL,
 storage_scope TEXT NOT NULL CHECK(storage_scope IN ('user','shared')),
 owner_key TEXT NOT NULL,
 storage_key TEXT NOT NULL,
 value_json TEXT NOT NULL,
 size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(application_slug,storage_scope,owner_key,storage_key));
CREATE INDEX IF NOT EXISTS idx_application_storage_namespace
 ON application_storage(application_slug,storage_scope,owner_key,storage_key);
CREATE TABLE IF NOT EXISTS application_scores (
 application_slug TEXT NOT NULL,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 best_score INTEGER NOT NULL CHECK(best_score>=0),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(application_slug,user_id));
CREATE INDEX IF NOT EXISTS idx_application_scores_leaderboard
 ON application_scores(application_slug,best_score DESC,updated_at,user_id);
CREATE TABLE IF NOT EXISTS installed_applications (
 slug TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 description TEXT NOT NULL,
 author TEXT NOT NULL,
 version TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('Games','Apps')),
 entrypoint TEXT NOT NULL, permissions_json TEXT NOT NULL DEFAULT '[]',
 capabilities_json TEXT NOT NULL DEFAULT '{}',
 installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS application_states (
 application_slug TEXT PRIMARY KEY,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS application_multiplayer_sessions (
 id TEXT PRIMARY KEY, application_slug TEXT NOT NULL, name TEXT NOT NULL,
 visibility TEXT NOT NULL CHECK(visibility IN ('public','private')), join_code TEXT,
 max_players INTEGER NOT NULL CHECK(max_players BETWEEN 2 AND 64),
 host_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 state_json TEXT NOT NULL DEFAULT '{}', state_revision INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_application_multiplayer_discovery
 ON application_multiplayer_sessions(application_slug,status,visibility,updated_at);
CREATE TABLE IF NOT EXISTS application_multiplayer_members (
 session_id TEXT NOT NULL REFERENCES application_multiplayer_sessions(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 connection_id TEXT NOT NULL, connected INTEGER NOT NULL DEFAULT 1 CHECK(connected IN (0,1)),
 joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(session_id,user_id));
CREATE TABLE IF NOT EXISTS application_multiplayer_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id TEXT NOT NULL REFERENCES application_multiplayer_sessions(id) ON DELETE CASCADE,
 sender_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_application_multiplayer_events
 ON application_multiplayer_events(session_id,id);
CREATE TABLE IF NOT EXISTS mud_characters (
 id TEXT PRIMARY KEY,
 user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
 display_name TEXT NOT NULL COLLATE NOCASE,
 creation_state TEXT NOT NULL DEFAULT 'pending' CHECK(creation_state IN ('pending','legacy','complete')),
 race_key TEXT,
 class_key TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_character_skills (
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 skill_key TEXT NOT NULL,
 rank INTEGER NOT NULL DEFAULT 1 CHECK(rank BETWEEN 1 AND 20),
 experience INTEGER NOT NULL DEFAULT 0 CHECK(experience>=0),
 PRIMARY KEY(character_id,skill_key));
CREATE TABLE IF NOT EXISTS mud_character_progression (
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 track_key TEXT NOT NULL CHECK(track_key IN ('shared','traditional','procedural')),
 level INTEGER NOT NULL DEFAULT 1 CHECK(level>=1),
 experience INTEGER NOT NULL DEFAULT 0 CHECK(experience>=0),
 current_health INTEGER NOT NULL DEFAULT 100 CHECK(current_health>=0),
 maximum_health INTEGER NOT NULL DEFAULT 100 CHECK(maximum_health>=1),
 strength INTEGER NOT NULL DEFAULT 10 CHECK(strength>=1),
 agility INTEGER NOT NULL DEFAULT 10 CHECK(agility>=1),
 intellect INTEGER NOT NULL DEFAULT 10 CHECK(intellect>=1),
 vitality INTEGER NOT NULL DEFAULT 10 CHECK(vitality>=1),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(character_id,track_key),
 CHECK(current_health<=maximum_health));
CREATE INDEX IF NOT EXISTS idx_mud_character_progression_track
 ON mud_character_progression(track_key,level,experience);
CREATE TABLE IF NOT EXISTS mud_builder_permissions (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_action_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 request_id TEXT NOT NULL,
 actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 action_key TEXT NOT NULL,
 input_method TEXT NOT NULL CHECK(input_method IN ('context','command')),
 parameters_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(actor_id,request_id));
CREATE INDEX IF NOT EXISTS idx_mud_action_actor ON mud_action_log(actor_id,id);
CREATE TABLE IF NOT EXISTS mud_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 action_id INTEGER REFERENCES mud_action_log(id) ON DELETE SET NULL,
 audience_type TEXT NOT NULL CHECK(audience_type IN ('private','station','room','instance')),
 audience_key TEXT NOT NULL,
 event_type TEXT NOT NULL,
 body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_events_audience
 ON mud_events(audience_type,audience_key,id);
CREATE TABLE IF NOT EXISTS mud_event_recipients (
 event_id INTEGER NOT NULL REFERENCES mud_events(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 PRIMARY KEY(event_id,user_id));
CREATE INDEX IF NOT EXISTS idx_mud_event_recipients_user
 ON mud_event_recipients(user_id,event_id);
CREATE TABLE IF NOT EXISTS mud_world_rooms (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 description TEXT NOT NULL,
 is_start INTEGER NOT NULL DEFAULT 0 CHECK(is_start IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mud_world_single_start
 ON mud_world_rooms(is_start) WHERE is_start=1;
CREATE TABLE IF NOT EXISTS mud_world_exits (
 id TEXT PRIMARY KEY,
 from_room_id TEXT NOT NULL REFERENCES mud_world_rooms(id) ON DELETE CASCADE,
 to_room_id TEXT NOT NULL REFERENCES mud_world_rooms(id) ON DELETE CASCADE,
 name TEXT NOT NULL COLLATE NOCASE,
 direction TEXT COLLATE NOCASE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(from_room_id,name),
 UNIQUE(from_room_id,direction));
CREATE INDEX IF NOT EXISTS idx_mud_world_exits_from
 ON mud_world_exits(from_room_id,id);
CREATE TABLE IF NOT EXISTS mud_world_locations (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 room_id TEXT REFERENCES mud_world_rooms(id) ON DELETE SET NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_procedural_instances (
 id TEXT PRIMARY KEY,
 instance_type TEXT NOT NULL CHECK(instance_type IN ('daily','random')),
 status TEXT NOT NULL DEFAULT 'pending'
 CHECK(status IN ('pending','active','completed','abandoned')),
 seed TEXT,
 theme_id TEXT,
 theme_version TEXT,
 template_hash TEXT,
 generator_version TEXT,
 layout TEXT,
 room_count INTEGER CHECK(room_count IS NULL OR room_count>=2),
 difficulty INTEGER CHECK(difficulty IS NULL OR difficulty>=1),
 generation_manifest_json TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_dungeon_template_versions (
 theme_id TEXT NOT NULL,
 version TEXT NOT NULL,
 template_hash TEXT NOT NULL,
 template_json TEXT NOT NULL,
 source_name TEXT NOT NULL,
 installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(theme_id,version),
 UNIQUE(theme_id,version,template_hash));
CREATE TABLE IF NOT EXISTS mud_dungeon_managed_definitions (
 definition_id TEXT PRIMARY KEY,
 theme_id TEXT NOT NULL,
 theme_version TEXT NOT NULL,
 definition_kind TEXT NOT NULL CHECK(definition_kind IN ('item','npc','enemy','conversation')),
 local_id TEXT NOT NULL,
 definition_hash TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(theme_id,theme_version,definition_kind,local_id));
CREATE TABLE IF NOT EXISTS mud_dungeon_rooms (
 instance_id TEXT NOT NULL REFERENCES mud_procedural_instances(id) ON DELETE CASCADE,
 room_key TEXT NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 x INTEGER NOT NULL,
 y INTEGER NOT NULL,
 role TEXT NOT NULL,
 depth INTEGER NOT NULL CHECK(depth>=0),
 difficulty INTEGER NOT NULL CHECK(difficulty>=1),
 name TEXT NOT NULL,
 description TEXT NOT NULL,
 room_template_id TEXT NOT NULL,
 environment_id TEXT,
 metadata_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(instance_id,room_key),
 UNIQUE(instance_id,ordinal),
 UNIQUE(instance_id,x,y));
CREATE INDEX IF NOT EXISTS idx_mud_dungeon_rooms_instance
 ON mud_dungeon_rooms(instance_id,ordinal);
CREATE TABLE IF NOT EXISTS mud_dungeon_exits (
 instance_id TEXT NOT NULL REFERENCES mud_procedural_instances(id) ON DELETE CASCADE,
 from_room_key TEXT NOT NULL,
 to_room_key TEXT NOT NULL,
 direction TEXT NOT NULL,
 name TEXT NOT NULL,
 PRIMARY KEY(instance_id,from_room_key,direction),
 FOREIGN KEY(instance_id,from_room_key)
  REFERENCES mud_dungeon_rooms(instance_id,room_key) ON DELETE CASCADE,
 FOREIGN KEY(instance_id,to_room_key)
  REFERENCES mud_dungeon_rooms(instance_id,room_key) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_mud_dungeon_exits_destination
 ON mud_dungeon_exits(instance_id,to_room_key);
CREATE TABLE IF NOT EXISTS mud_dungeon_content (
 id TEXT PRIMARY KEY,
 instance_id TEXT NOT NULL,
 room_key TEXT NOT NULL,
 content_type TEXT NOT NULL CHECK(content_type IN ('npc','enemy','item','encounter')),
 pool_id TEXT NOT NULL,
 definition_id TEXT,
 quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity>=1),
 snapshot_json TEXT NOT NULL DEFAULT '{}',
 config_json TEXT NOT NULL DEFAULT '{}',
 FOREIGN KEY(instance_id,room_key)
  REFERENCES mud_dungeon_rooms(instance_id,room_key) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_mud_dungeon_content_room
 ON mud_dungeon_content(instance_id,room_key,content_type,id);
CREATE TABLE IF NOT EXISTS mud_dungeon_generation_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 instance_id TEXT REFERENCES mud_procedural_instances(id) ON DELETE SET NULL,
 theme_id TEXT,
 theme_version TEXT,
 seed TEXT,
 request_json TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL CHECK(status IN ('success','failure')),
 message TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_dungeon_generation_log_status
 ON mud_dungeon_generation_log(status,id);
CREATE TABLE IF NOT EXISTS mud_expedition_participants (
 instance_id TEXT NOT NULL REFERENCES mud_procedural_instances(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'active'
 CHECK(status IN ('active','completed','failed','abandoned')),
 current_room_key TEXT NOT NULL,
 rooms_visited INTEGER NOT NULL DEFAULT 0 CHECK(rooms_visited>=0),
 joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT,
 reward_experience INTEGER NOT NULL DEFAULT 0 CHECK(reward_experience>=0),
 rewarded_at TEXT,
 PRIMARY KEY(instance_id,user_id),
 UNIQUE(instance_id,character_id),
 FOREIGN KEY(instance_id,current_room_key)
  REFERENCES mud_dungeon_rooms(instance_id,room_key) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_mud_expedition_participant_user
 ON mud_expedition_participants(user_id,status,joined_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mud_expedition_one_active_per_user
 ON mud_expedition_participants(user_id) WHERE status='active';
CREATE TABLE IF NOT EXISTS mud_expedition_room_visits (
 instance_id TEXT NOT NULL,
 room_key TEXT NOT NULL,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 visited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(instance_id,room_key,user_id),
 FOREIGN KEY(instance_id,user_id)
  REFERENCES mud_expedition_participants(instance_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(instance_id,room_key)
  REFERENCES mud_dungeon_rooms(instance_id,room_key) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS mud_dungeon_content_state (
 content_id TEXT PRIMARY KEY REFERENCES mud_dungeon_content(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'available'
 CHECK(status IN ('available','claimed','defeated')),
 claimed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_dungeon_conversation_sessions (
 character_id TEXT PRIMARY KEY REFERENCES mud_characters(id) ON DELETE CASCADE,
 instance_id TEXT NOT NULL REFERENCES mud_procedural_instances(id) ON DELETE CASCADE,
 content_id TEXT NOT NULL REFERENCES mud_dungeon_content(id) ON DELETE CASCADE,
 node_id TEXT NOT NULL REFERENCES mud_dialogue_nodes(id) ON DELETE CASCADE,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_dungeon_npc_player_states (
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 content_id TEXT NOT NULL REFERENCES mud_dungeon_content(id) ON DELETE CASCADE,
 state_json TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(character_id,content_id));
CREATE TABLE IF NOT EXISTS mud_player_locations (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 mode TEXT NOT NULL CHECK(mode IN ('traditional','procedural')),
 location_type TEXT NOT NULL
 CHECK(location_type IN ('world_room','portal','dungeon_instance')),
 location_key TEXT NOT NULL,
 instance_id TEXT REFERENCES mud_procedural_instances(id) ON DELETE SET NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_player_location_scope
 ON mud_player_locations(location_type,location_key,user_id);
CREATE TABLE IF NOT EXISTS mud_portal_settings (
 id INTEGER PRIMARY KEY CHECK(id=1),
 name TEXT NOT NULL DEFAULT 'Dungeon Portals',
 description TEXT NOT NULL DEFAULT 'A threshold of dormant gateways hums with unrealized possibility.',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_crossover_settings (
 id INTEGER PRIMARY KEY CHECK(id=1),
 progression_shared INTEGER NOT NULL DEFAULT 1 CHECK(progression_shared IN (0,1)),
 inventory_shared INTEGER NOT NULL DEFAULT 1 CHECK(inventory_shared IN (0,1)),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_daily_dungeon_settings (
 id INTEGER PRIMARY KEY CHECK(id=1),
 timezone TEXT NOT NULL DEFAULT 'UTC',
 station_seed TEXT NOT NULL,
 eligible_theme_ids_json TEXT NOT NULL DEFAULT '[]',
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 20),
 difficulty INTEGER CHECK(difficulty IS NULL OR difficulty BETWEEN 1 AND 100000),
 room_count INTEGER CHECK(room_count IS NULL OR room_count BETWEEN 2 AND 500),
 layout TEXT CHECK(layout IS NULL OR layout IN ('linear','branching','maze','hub')),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_daily_dungeons (
 day_key TEXT PRIMARY KEY,
 timezone TEXT NOT NULL,
 design_instance_id TEXT UNIQUE REFERENCES mud_procedural_instances(id) ON DELETE SET NULL,
 status TEXT NOT NULL CHECK(status IN ('ready','failed')),
 theme_id TEXT,
 theme_version TEXT,
 template_hash TEXT,
 seed TEXT NOT NULL,
 error_message TEXT,
 generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((status='ready' AND design_instance_id IS NOT NULL AND theme_id IS NOT NULL)
    OR (status='failed' AND error_message IS NOT NULL)));
CREATE INDEX IF NOT EXISTS idx_mud_daily_dungeons_generated
 ON mud_daily_dungeons(generated_at,day_key);
CREATE TABLE IF NOT EXISTS mud_daily_runs (
 instance_id TEXT PRIMARY KEY REFERENCES mud_procedural_instances(id) ON DELETE CASCADE,
 day_key TEXT NOT NULL REFERENCES mud_daily_dungeons(day_key) ON DELETE RESTRICT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_daily_runs_day ON mud_daily_runs(day_key,created_at);
CREATE TABLE IF NOT EXISTS mud_daily_participation (
 day_key TEXT NOT NULL REFERENCES mud_daily_dungeons(day_key) ON DELETE RESTRICT,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 completions INTEGER NOT NULL DEFAULT 0 CHECK(completions>=0),
 failures INTEGER NOT NULL DEFAULT 0 CHECK(failures>=0),
 abandons INTEGER NOT NULL DEFAULT 0 CHECK(abandons>=0),
 best_rooms_visited INTEGER NOT NULL DEFAULT 0 CHECK(best_rooms_visited>=0),
 last_instance_id TEXT REFERENCES mud_procedural_instances(id) ON DELETE SET NULL,
 last_result TEXT CHECK(last_result IS NULL OR last_result IN ('active','completed','failed','abandoned')),
 first_started_at TEXT,
 last_finished_at TEXT,
 reward_experience INTEGER NOT NULL DEFAULT 0 CHECK(reward_experience>=0),
 rewarded_at TEXT,
 PRIMARY KEY(day_key,user_id));
CREATE INDEX IF NOT EXISTS idx_mud_daily_participation_user
 ON mud_daily_participation(user_id,day_key);
CREATE TABLE IF NOT EXISTS mud_presence (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 connection_id TEXT NOT NULL,
 client_id TEXT NOT NULL,
 room_id TEXT REFERENCES mud_world_rooms(id) ON DELETE CASCADE,
 location_type TEXT NOT NULL DEFAULT 'world_room',
 location_key TEXT,
 instance_id TEXT REFERENCES mud_procedural_instances(id) ON DELETE SET NULL,
 last_seen TEXT NOT NULL,
 disconnected_at TEXT,
 PRIMARY KEY(user_id,connection_id,client_id));
CREATE INDEX IF NOT EXISTS idx_mud_presence_room
 ON mud_presence(room_id,user_id);
CREATE INDEX IF NOT EXISTS idx_mud_presence_seen
 ON mud_presence(last_seen,disconnected_at);
CREATE TABLE IF NOT EXISTS mud_item_definitions (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL COLLATE NOCASE,
 description TEXT NOT NULL,
 item_type TEXT NOT NULL DEFAULT 'misc',
 properties_json TEXT NOT NULL DEFAULT '{}',
 behaviors_json TEXT NOT NULL DEFAULT '{}',
 equipment_slot TEXT,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(item_type IN ('misc','equipment','container','consumable','key')),
 CHECK(equipment_slot IS NULL OR equipment_slot IN ('head','body','hands','legs','feet','main_hand','off_hand','accessory'))
);
CREATE INDEX IF NOT EXISTS idx_mud_item_definitions_name ON mud_item_definitions(name);
CREATE TABLE IF NOT EXISTS mud_item_instances (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES mud_item_definitions(id) ON DELETE RESTRICT,
 room_id TEXT REFERENCES mud_world_rooms(id) ON DELETE SET NULL,
 owner_character_id TEXT REFERENCES mud_characters(id) ON DELETE CASCADE,
 container_id TEXT REFERENCES mud_item_instances(id) ON DELETE SET NULL,
 equipped_slot TEXT,
 is_open INTEGER NOT NULL DEFAULT 0 CHECK(is_open IN (0,1)),
 state_json TEXT NOT NULL DEFAULT '{}',
 possession_mode TEXT NOT NULL DEFAULT 'traditional'
 CHECK(possession_mode IN ('traditional','procedural')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((room_id IS NOT NULL) + (owner_character_id IS NOT NULL AND equipped_slot IS NULL) + (container_id IS NOT NULL) + (owner_character_id IS NOT NULL AND equipped_slot IS NOT NULL) = 1),
 CHECK(equipped_slot IS NULL OR equipped_slot IN ('head','body','hands','legs','feet','main_hand','off_hand','accessory'))
);
CREATE INDEX IF NOT EXISTS idx_mud_item_instances_room ON mud_item_instances(room_id,id);
CREATE INDEX IF NOT EXISTS idx_mud_item_instances_owner ON mud_item_instances(owner_character_id,id);
CREATE INDEX IF NOT EXISTS idx_mud_item_instances_container ON mud_item_instances(container_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mud_equipment_slot
 ON mud_item_instances(owner_character_id,equipped_slot) WHERE equipped_slot IS NOT NULL;
CREATE TABLE IF NOT EXISTS mud_character_equipment (
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 loadout_key TEXT NOT NULL CHECK(loadout_key IN ('shared','traditional','procedural')),
 slot TEXT NOT NULL CHECK(slot IN ('head','body','hands','legs','feet','main_hand','off_hand','accessory')),
 item_instance_id TEXT NOT NULL REFERENCES mud_item_instances(id) ON DELETE CASCADE,
 equipped_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(character_id,loadout_key,slot),
 UNIQUE(character_id,loadout_key,item_instance_id));
CREATE INDEX IF NOT EXISTS idx_mud_character_equipment_item
 ON mud_character_equipment(item_instance_id);
CREATE TABLE IF NOT EXISTS mud_conversations (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL COLLATE NOCASE,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_dialogue_nodes (
 id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES mud_conversations(id) ON DELETE CASCADE,
 node_key TEXT NOT NULL COLLATE NOCASE,
 body TEXT NOT NULL,
 is_start INTEGER NOT NULL DEFAULT 0 CHECK(is_start IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(conversation_id,node_key));
CREATE UNIQUE INDEX IF NOT EXISTS idx_mud_dialogue_single_start
 ON mud_dialogue_nodes(conversation_id,is_start) WHERE is_start=1;
CREATE TABLE IF NOT EXISTS mud_npc_definitions (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL COLLATE NOCASE,
 description TEXT NOT NULL,
 conversation_id TEXT REFERENCES mud_conversations(id) ON DELETE SET NULL,
 properties_json TEXT NOT NULL DEFAULT '{}',
 behaviors_json TEXT NOT NULL DEFAULT '{}',
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_npc_definitions_name ON mud_npc_definitions(name);
CREATE TABLE IF NOT EXISTS mud_npc_instances (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES mud_npc_definitions(id) ON DELETE RESTRICT,
 room_id TEXT NOT NULL REFERENCES mud_world_rooms(id) ON DELETE CASCADE,
 state_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_npc_instances_room ON mud_npc_instances(room_id,id);
CREATE TABLE IF NOT EXISTS mud_npc_combat_profiles (
 npc_definition_id TEXT PRIMARY KEY REFERENCES mud_npc_definitions(id) ON DELETE CASCADE,
 maximum_health INTEGER NOT NULL DEFAULT 30 CHECK(maximum_health BETWEEN 1 AND 1000000),
 attack INTEGER NOT NULL DEFAULT 6 CHECK(attack BETWEEN 1 AND 100000),
 defense INTEGER NOT NULL DEFAULT 2 CHECK(defense BETWEEN 0 AND 100000),
 experience_reward INTEGER NOT NULL DEFAULT 10 CHECK(experience_reward BETWEEN 0 AND 100000),
 respawn_seconds INTEGER NOT NULL DEFAULT 60 CHECK(respawn_seconds BETWEEN 5 AND 86400),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_npc_equipment (
 npc_definition_id TEXT NOT NULL REFERENCES mud_npc_definitions(id) ON DELETE CASCADE,
 equipment_slot TEXT NOT NULL CHECK(equipment_slot IN ('head','body','hands','legs','feet','main_hand','off_hand','accessory')),
 item_definition_id TEXT NOT NULL REFERENCES mud_item_definitions(id) ON DELETE RESTRICT,
 PRIMARY KEY(npc_definition_id,equipment_slot));
CREATE TABLE IF NOT EXISTS mud_npc_combat_rewards (
 id TEXT PRIMARY KEY,
 npc_definition_id TEXT NOT NULL REFERENCES mud_npc_definitions(id) ON DELETE CASCADE,
 reward_type TEXT NOT NULL CHECK(reward_type IN ('experience','item')),
 target_id TEXT REFERENCES mud_item_definitions(id) ON DELETE RESTRICT,
 amount INTEGER NOT NULL DEFAULT 1 CHECK(amount BETWEEN 1 AND 100000),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((reward_type='experience' AND target_id IS NULL) OR
       (reward_type='item' AND target_id IS NOT NULL)));
CREATE INDEX IF NOT EXISTS idx_mud_npc_combat_rewards_definition
 ON mud_npc_combat_rewards(npc_definition_id,id);
CREATE TABLE IF NOT EXISTS mud_combat_encounters (
 id TEXT PRIMARY KEY,
 scope_type TEXT NOT NULL CHECK(scope_type IN ('room','instance')),
 scope_key TEXT NOT NULL,
 npc_instance_id TEXT REFERENCES mud_npc_instances(id) ON DELETE CASCADE,
 npc_definition_id TEXT NOT NULL REFERENCES mud_npc_definitions(id) ON DELETE RESTRICT,
 status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('ready','active','defeated','abandoned')),
 current_health INTEGER NOT NULL CHECK(current_health>=0),
 maximum_health INTEGER NOT NULL CHECK(maximum_health>=1 AND current_health<=maximum_health),
 started_at TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 ended_at TEXT,
 CHECK((scope_type='room' AND npc_instance_id IS NOT NULL) OR scope_type='instance'));
CREATE UNIQUE INDEX IF NOT EXISTS idx_mud_combat_live_enemy
 ON mud_combat_encounters(npc_instance_id) WHERE status IN ('ready','active');
CREATE INDEX IF NOT EXISTS idx_mud_combat_scope
 ON mud_combat_encounters(scope_type,scope_key,status);
CREATE TABLE IF NOT EXISTS mud_combat_participants (
 encounter_id TEXT NOT NULL REFERENCES mud_combat_encounters(id) ON DELETE CASCADE,
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 damage_dealt INTEGER NOT NULL DEFAULT 0 CHECK(damage_dealt>=0),
 joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_action_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 left_at TEXT,
 defeated_at TEXT,
 rewarded_at TEXT,
 PRIMARY KEY(encounter_id,character_id));
CREATE INDEX IF NOT EXISTS idx_mud_combat_participant_character
 ON mud_combat_participants(character_id,left_at,encounter_id);
CREATE TABLE IF NOT EXISTS mud_quests (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL COLLATE NOCASE,
 description TEXT NOT NULL,
 prerequisite_quest_id TEXT REFERENCES mud_quests(id) ON DELETE SET NULL,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_quests_name ON mud_quests(name);
CREATE TABLE IF NOT EXISTS mud_quest_objectives (
 id TEXT PRIMARY KEY,
 quest_id TEXT NOT NULL REFERENCES mud_quests(id) ON DELETE CASCADE,
 objective_type TEXT NOT NULL CHECK(objective_type IN ('talk_npc','visit_room','possess_item')),
 target_id TEXT NOT NULL,
 description TEXT NOT NULL,
 required_count INTEGER NOT NULL DEFAULT 1 CHECK(required_count>=1),
 sort_order INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_quest_objectives_quest
 ON mud_quest_objectives(quest_id,sort_order,id);
CREATE TABLE IF NOT EXISTS mud_quest_rewards (
 id TEXT PRIMARY KEY,
 quest_id TEXT NOT NULL REFERENCES mud_quests(id) ON DELETE CASCADE,
 reward_type TEXT NOT NULL CHECK(reward_type IN ('experience','item')),
 target_id TEXT,
 amount INTEGER NOT NULL DEFAULT 1 CHECK(amount>=1),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_quest_rewards_quest ON mud_quest_rewards(quest_id,id);
CREATE TABLE IF NOT EXISTS mud_quest_givers (
 quest_id TEXT NOT NULL REFERENCES mud_quests(id) ON DELETE CASCADE,
 npc_definition_id TEXT NOT NULL REFERENCES mud_npc_definitions(id) ON DELETE CASCADE,
 PRIMARY KEY(quest_id,npc_definition_id));
CREATE TABLE IF NOT EXISTS mud_dialogue_responses (
 id TEXT PRIMARY KEY,
 node_id TEXT NOT NULL REFERENCES mud_dialogue_nodes(id) ON DELETE CASCADE,
 response_text TEXT NOT NULL,
 next_node_id TEXT REFERENCES mud_dialogue_nodes(id) ON DELETE SET NULL,
 accept_quest_id TEXT REFERENCES mud_quests(id) ON DELETE SET NULL,
 required_state_key TEXT,
 required_state_value TEXT,
 state_key TEXT,
 state_value TEXT,
 sort_order INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mud_dialogue_responses_node
 ON mud_dialogue_responses(node_id,sort_order,id);
CREATE TABLE IF NOT EXISTS mud_conversation_sessions (
 character_id TEXT PRIMARY KEY REFERENCES mud_characters(id) ON DELETE CASCADE,
 npc_instance_id TEXT NOT NULL REFERENCES mud_npc_instances(id) ON DELETE CASCADE,
 node_id TEXT NOT NULL REFERENCES mud_dialogue_nodes(id) ON DELETE CASCADE,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mud_npc_player_states (
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 npc_instance_id TEXT NOT NULL REFERENCES mud_npc_instances(id) ON DELETE CASCADE,
 state_json TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(character_id,npc_instance_id));
CREATE TABLE IF NOT EXISTS mud_player_quests (
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 quest_id TEXT NOT NULL REFERENCES mud_quests(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed')),
 accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 completed_at TEXT,
 rewarded_at TEXT,
 PRIMARY KEY(character_id,quest_id));
CREATE INDEX IF NOT EXISTS idx_mud_player_quests_status
 ON mud_player_quests(character_id,status,accepted_at);
CREATE TABLE IF NOT EXISTS mud_player_objectives (
 character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,
 objective_id TEXT NOT NULL REFERENCES mud_quest_objectives(id) ON DELETE CASCADE,
 progress INTEGER NOT NULL DEFAULT 0 CHECK(progress>=0),
 completed_at TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(character_id,objective_id));
CREATE TRIGGER IF NOT EXISTS radio_default_membership_after_user_insert
AFTER INSERT ON users
BEGIN
 INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id)
 SELECT NEW.id,id FROM radio_permission_groups WHERE is_default=1;
END;
CREATE TRIGGER IF NOT EXISTS mud_character_after_user_insert
AFTER INSERT ON users
BEGIN
 INSERT OR IGNORE INTO mud_characters(id,user_id,display_name,creation_state)
 VALUES('stationnet:character:' || NEW.id,NEW.id,NEW.callsign,'pending');
 INSERT OR IGNORE INTO mud_character_progression(character_id,track_key)
 VALUES('stationnet:character:' || NEW.id,'shared');
 INSERT OR IGNORE INTO mud_character_progression(character_id,track_key)
 VALUES('stationnet:character:' || NEW.id,'traditional');
 INSERT OR IGNORE INTO mud_character_progression(character_id,track_key)
 VALUES('stationnet:character:' || NEW.id,'procedural');
END;
"""

DEFAULT_RADIO_BANDS = (
    (1, "GENERAL COMMUNICATION", 145000, 432990, "members", 0, 0, 1, 0, 0),
    (2, "BROADCASTS", 7000, 13990, "admins", 0, 0, 0, 1, 0),
    (3, "MORSE / CW", 14000, 27990, "members", 1, 0, 0, 0, 0),
    (4, "DATA TRANSMISSIONS", 28000, 144990, "members", 0, 1, 0, 0, 0),
    (5, "EXPERIMENTAL / UNRESTRICTED", 433000, 999990, "members", 1, 1, 0, 0, 1),
)

NARROW_DEFAULT_RADIO_BANDS = (
    (1, "GENERAL COMMUNICATION", 145000, 145990, "members"),
    (2, "BROADCASTS", 7000, 7990, "admins"),
    (3, "MORSE / CW", 14000, 14990, "members"),
    (4, "DATA TRANSMISSIONS", 28000, 28990, "members"),
    (5, "EXPERIMENTAL / UNRESTRICTED", 433000, 433990, "members"),
)

LEGACY_DEFAULT_RADIO_BANDS = (
    (1, "GENERAL COMMUNICATION", 144000, 144399, "members"),
    (2, "BROADCASTS", 144400, 144599, "admins"),
    (3, "MORSE / CW", 144600, 144799, "members"),
    (4, "DATA TRANSMISSIONS", 144800, 144999, "members"),
    (5, "EXPERIMENTAL / UNRESTRICTED", 145000, 145999, "members"),
)

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA busy_timeout=5000")
    return g.db

def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()

def init_db():
    database = get_db()
    database.executescript(SCHEMA)
    _migrate_mud_dungeon_content_types(database)
    _migrate_station_customization(database)
    _migrate_user_feeds(database)
    _migrate_registration_policy(database)
    _migrate_community_fields(database)
    _migrate_mail_threads(database)
    _migrate_hosted_broadcast_fields(database)
    _migrate_broadcast_identity_fields(database)
    _migrate_mud_settings(database)
    _migrate_mud_locations(database)
    _migrate_mud_dungeon_instances(database)
    _migrate_mud_expeditions(database)
    _migrate_mud_crossover(database)
    _migrate_application_permissions(database)
    _migrate_mud_character_creation(database)
    _initialize_mud_characters(database)
    _initialize_mud_world(database)
    database.execute("INSERT OR IGNORE INTO mud_portal_settings(id) VALUES(1)")
    database.execute("INSERT OR IGNORE INTO mud_crossover_settings(id) VALUES(1)")
    database.execute(
        "INSERT OR IGNORE INTO mud_daily_dungeon_settings(id,station_seed) VALUES(1,?)",
        (secrets.token_hex(32),),
    )
    _migrate_legacy_callsigns(database)
    _migrate_factory_radio_bands(database)
    _align_radio_bands_to_display_precision(database)
    added_band_capabilities, added_signal_capabilities, added_group_capability = \
        _migrate_radio_capability_columns(database)
    database.executemany(
        "INSERT OR IGNORE INTO radio_bands(id,name,start_khz,end_khz,tx_permission,"
        "supports_cw,supports_sstv,supports_live_voice,supports_hosted_broadcasts,"
        "allow_open_broadcasts) VALUES(?,?,?,?,?,?,?,?,?,?)",
        DEFAULT_RADIO_BANDS,
    )
    _migrate_radio_permissions(database)
    if added_band_capabilities:
        database.execute(
            "UPDATE radio_bands SET supports_live_voice=(name='GENERAL COMMUNICATION'),"
            "supports_hosted_broadcasts=(name='BROADCASTS')"
        )
    if added_signal_capabilities:
        database.execute(
            "UPDATE radio_bands SET supports_cw="
            "(name IN ('MORSE / CW','EXPERIMENTAL / UNRESTRICTED'))"
            ",supports_sstv=(name IN ('DATA TRANSMISSIONS','EXPERIMENTAL / UNRESTRICTED'))"
        )
    if added_group_capability:
        database.execute(
            "UPDATE radio_permission_groups SET host_broadcasts=1 WHERE name='OPERATORS'"
        )
    database.commit()


def _migrate_mud_dungeon_content_types(database):
    """Allow non-hostile embedded NPCs in existing Chunk 10 databases."""
    sql = database.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='mud_dungeon_content'"
    ).fetchone()["sql"]
    if "'npc'" in sql:
        return
    database.commit()
    database.execute("PRAGMA foreign_keys=OFF")
    database.execute("PRAGMA legacy_alter_table=ON")
    try:
        database.executescript("""
        DROP INDEX IF EXISTS idx_mud_dungeon_content_room;
        ALTER TABLE mud_dungeon_content RENAME TO mud_dungeon_content_legacy;
        CREATE TABLE mud_dungeon_content (
         id TEXT PRIMARY KEY,
         instance_id TEXT NOT NULL,
         room_key TEXT NOT NULL,
         content_type TEXT NOT NULL CHECK(content_type IN ('npc','enemy','item','encounter')),
         pool_id TEXT NOT NULL,
         definition_id TEXT,
         quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity>=1),
         snapshot_json TEXT NOT NULL DEFAULT '{}',
         config_json TEXT NOT NULL DEFAULT '{}',
         FOREIGN KEY(instance_id,room_key)
          REFERENCES mud_dungeon_rooms(instance_id,room_key) ON DELETE CASCADE);
        INSERT INTO mud_dungeon_content
        SELECT * FROM mud_dungeon_content_legacy;
        DROP TABLE mud_dungeon_content_legacy;
        CREATE INDEX idx_mud_dungeon_content_room
         ON mud_dungeon_content(instance_id,room_key,content_type,id);
        """)
    finally:
        database.execute("PRAGMA legacy_alter_table=OFF")
        database.execute("PRAGMA foreign_keys=ON")


def _migrate_application_permissions(database):
    """Add manifest metadata to installations created by older releases."""
    columns = {row["name"] for row in database.execute("PRAGMA table_info(installed_applications)")}
    if "permissions_json" not in columns:
        database.execute(
            "ALTER TABLE installed_applications ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "capabilities_json" not in columns:
        database.execute(
            "ALTER TABLE installed_applications ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}'"
        )


def _migrate_mail_threads(database):
    """Turn legacy standalone mail into one-message conversations."""
    columns = {row["name"] for row in database.execute("PRAGMA table_info(mail)")}
    if "thread_id" not in columns:
        database.execute("ALTER TABLE mail ADD COLUMN thread_id INTEGER REFERENCES mail(id)")
    if "reply_to_id" not in columns:
        database.execute("ALTER TABLE mail ADD COLUMN reply_to_id INTEGER REFERENCES mail(id)")
    database.execute("UPDATE mail SET thread_id=id WHERE thread_id IS NULL")
    database.execute(
        "CREATE INDEX IF NOT EXISTS idx_mail_thread ON mail(thread_id,created_at,id)"
    )


def _migrate_mud_character_creation(database):
    """Offer existing characters one customization without resetting their saves."""
    columns = {row["name"] for row in database.execute("PRAGMA table_info(mud_characters)")}
    if "creation_state" not in columns:
        database.execute(
            "ALTER TABLE mud_characters ADD COLUMN creation_state TEXT NOT NULL DEFAULT 'pending'"
        )
        database.execute("UPDATE mud_characters SET creation_state='legacy'")
    if "race_key" not in columns:
        database.execute("ALTER TABLE mud_characters ADD COLUMN race_key TEXT")
    if "class_key" not in columns:
        database.execute("ALTER TABLE mud_characters ADD COLUMN class_key TEXT")
    database.execute(
        "CREATE TABLE IF NOT EXISTS mud_character_skills ("
        "character_id TEXT NOT NULL REFERENCES mud_characters(id) ON DELETE CASCADE,"
        "skill_key TEXT NOT NULL,rank INTEGER NOT NULL DEFAULT 1 CHECK(rank BETWEEN 1 AND 20),"
        "experience INTEGER NOT NULL DEFAULT 0 CHECK(experience>=0),"
        "PRIMARY KEY(character_id,skill_key))"
    )


def _initialize_mud_characters(database):
    """Backfill one identity and non-destructive mode-ready tracks per account."""
    database.execute(
        "INSERT OR IGNORE INTO mud_characters(id,user_id,display_name,creation_state) "
        "SELECT 'stationnet:character:' || id,id,callsign,'pending' FROM users"
    )
    database.execute(
        "INSERT OR IGNORE INTO mud_character_progression(character_id,track_key) "
        "SELECT c.id,t.track_key FROM mud_characters AS c CROSS JOIN ("
        "SELECT 'shared' AS track_key UNION ALL SELECT 'traditional' "
        "UNION ALL SELECT 'procedural') AS t"
    )


def _initialize_mud_world(database):
    """Install only the generic authored-world entry room."""
    if database.execute("SELECT 1 FROM mud_world_rooms WHERE is_start=1").fetchone() is None:
        existing = database.execute(
            "SELECT 1 FROM mud_world_rooms WHERE id='stationnet:start'"
        ).fetchone()
        if existing is not None:
            database.execute(
                "UPDATE mud_world_rooms SET is_start=1,updated_at=CURRENT_TIMESTAMP "
                "WHERE id='stationnet:start'"
            )
            return
        database.execute(
            "INSERT INTO mud_world_rooms(id,name,description,is_start) VALUES(?,?,?,1)",
            (
                "stationnet:start",
                "Starting Room",
                "A quiet, unadorned room waiting for this station's world to take shape.",
            ),
        )


def _migrate_radio_capability_columns(database):
    """Add voice/broadcast preparation fields without resetting later sysop choices."""
    band_columns = {row["name"] for row in database.execute("PRAGMA table_info(radio_bands)")}
    added_bands = "supports_live_voice" not in band_columns
    added_signals = "supports_cw" not in band_columns or "supports_sstv" not in band_columns
    if "supports_cw" not in band_columns:
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN supports_cw INTEGER NOT NULL DEFAULT 0 "
            "CHECK(supports_cw IN (0,1))"
        )
    if "supports_sstv" not in band_columns:
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN supports_sstv INTEGER NOT NULL DEFAULT 0 "
            "CHECK(supports_sstv IN (0,1))"
        )
    if added_bands:
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN supports_live_voice INTEGER NOT NULL DEFAULT 0 "
            "CHECK(supports_live_voice IN (0,1))"
        )
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN supports_hosted_broadcasts INTEGER NOT NULL DEFAULT 0 "
            "CHECK(supports_hosted_broadcasts IN (0,1))"
        )

    group_columns = {
        row["name"] for row in database.execute("PRAGMA table_info(radio_permission_groups)")
    }
    added_groups = "host_broadcasts" not in group_columns
    if added_groups:
        database.execute(
            "ALTER TABLE radio_permission_groups ADD COLUMN host_broadcasts INTEGER NOT NULL DEFAULT 0 "
            "CHECK(host_broadcasts IN (0,1))"
        )
    return added_bands, added_signals, added_groups


def _migrate_factory_radio_bands(database):
    """Expand known factory plans while preserving operator customizations."""
    rows = database.execute(
        "SELECT id,name,start_khz,end_khz,tx_permission FROM radio_bands ORDER BY id"
    ).fetchall()
    current = [tuple(row) for row in rows]
    if current not in (list(LEGACY_DEFAULT_RADIO_BANDS), list(NARROW_DEFAULT_RADIO_BANDS)):
        return
    database.executemany(
        "UPDATE radio_bands SET name=?,start_khz=?,end_khz=?,tx_permission=? WHERE id=?",
        [(name, start, end, permission, band_id)
         for band_id, name, start, end, permission, _cw, _sstv, _voice, _broadcast, _open
         in DEFAULT_RADIO_BANDS],
    )


def _align_radio_bands_to_display_precision(database):
    """Minimally align legacy custom limits with the two-decimal tuning grid."""
    for row in database.execute("SELECT id,start_khz,end_khz FROM radio_bands").fetchall():
        start = min(999990, ((row["start_khz"] + 9) // 10) * 10)
        end = min(999990, (row["end_khz"] // 10) * 10)
        if start > end:
            point = min(999990, max(100, ((row["start_khz"] + 5) // 10) * 10))
            start = end = point
        if start != row["start_khz"] or end != row["end_khz"]:
            database.execute(
                "UPDATE radio_bands SET start_khz=?,end_khz=? WHERE id=?",
                (start, end, row["id"]),
            )


def _migrate_radio_permissions(database):
    """Create baseline groups/rules and guarantee DEFAULT membership for every account."""
    default = database.execute(
        "SELECT id FROM radio_permission_groups WHERE is_default=1"
    ).fetchone()
    first_install = default is None
    if first_install:
        cursor = database.execute(
            "INSERT INTO radio_permission_groups(name,is_default) VALUES('DEFAULT',1)"
        )
        default_id = cursor.lastrowid
        operator_id = database.execute(
            "INSERT INTO radio_permission_groups(name,host_broadcasts) VALUES('OPERATORS',1)"
        ).lastrowid
        bands = database.execute(
            "SELECT id,tx_permission FROM radio_bands ORDER BY id"
        ).fetchall()
        for band in bands:
            baseline = "FULL" if band["tx_permission"] == "members" else "LISTEN"
            database.execute(
                "INSERT INTO radio_permission_rules(group_id,scope_type,band_id,permission) "
                "VALUES(?, 'band', ?, ?)", (default_id, band["id"], baseline),
            )
            if band["tx_permission"] == "admins":
                database.execute(
                    "INSERT INTO radio_permission_rules(group_id,scope_type,band_id,permission) "
                    "VALUES(?, 'band', ?, 'FULL')", (operator_id, band["id"]),
                )
        database.execute(
            "INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id) "
            "SELECT id,? FROM users WHERE is_admin=1", (operator_id,),
        )
    else:
        default_id = default["id"]
    database.execute(
        "INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id) "
        "SELECT id,? FROM users", (default_id,),
    )

def _migrate_user_feeds(database):
    columns = {row["name"] for row in database.execute("PRAGMA table_info(station)")}
    if "user_feeds_enabled" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN user_feeds_enabled INTEGER NOT NULL DEFAULT 1 "
            "CHECK(user_feeds_enabled IN (0,1))"
        )
    # SQLite cannot alter a CHECK constraint in place. Rebuild only the report
    # table on older installations so feed content can use the existing queue.
    sql = database.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='moderation_reports'"
    ).fetchone()[0]
    if "'feed_post'" not in sql:
        database.execute("ALTER TABLE moderation_reports RENAME TO moderation_reports_old")
        database.execute("""CREATE TABLE moderation_reports (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         reporter_id INTEGER NOT NULL REFERENCES users(id),
         target_type TEXT NOT NULL CHECK(target_type IN
           ('user','chat','bulletin','file','feed_post','feed_comment')),
         target_id INTEGER NOT NULL, subject_user_id INTEGER REFERENCES users(id),
         reason TEXT NOT NULL, created_at TEXT NOT NULL,
         status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
         resolver_id INTEGER REFERENCES users(id), resolution_note TEXT NOT NULL DEFAULT '',
         resolved_at TEXT)""")
        database.execute("INSERT INTO moderation_reports SELECT * FROM moderation_reports_old")
        database.execute("DROP TABLE moderation_reports_old")
        database.execute(
            "CREATE INDEX idx_moderation_reports_queue ON moderation_reports(status,id)"
        )
        database.execute(
            "CREATE UNIQUE INDEX idx_moderation_reports_open_unique "
            "ON moderation_reports(reporter_id,target_type,target_id) WHERE status='open'"
        )


def _migrate_station_customization(database):
    """Add customizable station copy to databases created by older versions."""
    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(station)")
    }
    if "landing_message" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN landing_message TEXT NOT NULL DEFAULT ''"
        )
    if "landing_heading" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN landing_heading TEXT NOT NULL DEFAULT ''"
        )
    if "landing_message_enabled" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN landing_message_enabled INTEGER NOT NULL DEFAULT 1 "
            "CHECK(landing_message_enabled IN (0,1))"
        )
    if "brand_tagline" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN brand_tagline TEXT NOT NULL "
            "DEFAULT 'H7/dS! // YOUR INDEPENDENT COMMUNITY STATION //'"
        )
    if "header_font" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN header_font TEXT NOT NULL DEFAULT 'small'"
        )
    if "header_font_size" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN header_font_size INTEGER NOT NULL DEFAULT 100"
        )
    if "header_text" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN header_text TEXT NOT NULL DEFAULT ''"
        )
    if "header_color" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN header_color TEXT NOT NULL DEFAULT ''"
        )
    database.execute(
        "UPDATE station SET brand_tagline=? WHERE brand_tagline=?",
        (
            "H7/dS! // YOUR INDEPENDENT COMMUNITY STATION //",
            "H7/dS! // YOUR INDEPENDENT COMMUNITY NODE //",
        ),
    )


def _migrate_registration_policy(database):
    """Upgrade the legacy open/closed switch without changing its behavior."""
    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(station)")
    }
    if "registration_mode" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN registration_mode TEXT NOT NULL DEFAULT 'open'"
        )
        database.execute(
            "UPDATE station SET registration_mode="
            "CASE WHEN registration_open=1 THEN 'open' ELSE 'disabled' END"
        )

def _migrate_community_fields(database):
    """Add Milestone 2 settings and profile fields to existing stations."""
    station_columns = {row["name"] for row in database.execute("PRAGMA table_info(station)")}
    additions = {
        "upload_permission": "TEXT NOT NULL DEFAULT 'members'",
        "file_storage_limit_mb": "INTEGER NOT NULL DEFAULT 100",
        "file_size_limit_mb": "INTEGER NOT NULL DEFAULT 10",
        "radio_default_quota_mb": "INTEGER NOT NULL DEFAULT 100",
    }
    for name, declaration in additions.items():
        if name not in station_columns:
            database.execute(f"ALTER TABLE station ADD COLUMN {name} {declaration}")

    user_columns = {row["name"] for row in database.execute("PRAGMA table_info(users)")}
    user_additions = {
        "tagline": "TEXT NOT NULL DEFAULT ''",
        "about": "TEXT NOT NULL DEFAULT ''",
        "birthday": "TEXT",
        "gender": "TEXT NOT NULL DEFAULT ''",
        "last_seen": "TEXT",
        "connection_id": "TEXT",
        "radio_quota_mb": "INTEGER",
        "profile_photo_filename": "TEXT",
    }
    for name, declaration in user_additions.items():
        if name not in user_columns:
            database.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")

def _migrate_hosted_broadcast_fields(database):
    """Add hosted-radio controls without overwriting established band choices."""
    station_columns = {row["name"] for row in database.execute("PRAGMA table_info(station)")}
    if "radio_broadcast_limit_minutes" not in station_columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN radio_broadcast_limit_minutes INTEGER NOT NULL DEFAULT 120"
        )
    band_columns = {row["name"] for row in database.execute("PRAGMA table_info(radio_bands)")}
    if "allow_open_broadcasts" not in band_columns:
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN allow_open_broadcasts INTEGER NOT NULL DEFAULT 0 "
            "CHECK(allow_open_broadcasts IN (0,1))"
        )
        database.execute(
            "UPDATE radio_bands SET allow_open_broadcasts=1 "
            "WHERE name='EXPERIMENTAL / UNRESTRICTED'"
        )
    audio_columns = {row["name"] for row in database.execute("PRAGMA table_info(radio_audio_files)")}
    if "duration_seconds" not in audio_columns:
        database.execute(
            "ALTER TABLE radio_audio_files ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 180"
        )
    if "title" not in audio_columns:
        database.execute(
            "ALTER TABLE radio_audio_files ADD COLUMN title TEXT NOT NULL DEFAULT ''"
        )
    if "artist" not in audio_columns:
        database.execute(
            "ALTER TABLE radio_audio_files ADD COLUMN artist TEXT NOT NULL DEFAULT ''"
        )
    playlist_columns = {row["name"] for row in database.execute("PRAGMA table_info(radio_playlists)")}
    if "shuffle_enabled" not in playlist_columns:
        database.execute(
            "ALTER TABLE radio_playlists ADD COLUMN shuffle_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK(shuffle_enabled IN (0,1))"
        )

def _migrate_broadcast_identity_fields(database):
    """Add listener-facing labels to existing frequency assignments in place."""
    columns = {
        row["name"] for row in database.execute(
            "PRAGMA table_info(radio_broadcast_assignments)"
        )
    }
    if "broadcast_name" not in columns:
        database.execute(
            "ALTER TABLE radio_broadcast_assignments "
            "ADD COLUMN broadcast_name TEXT NOT NULL DEFAULT ''"
        )
    if "broadcast_message" not in columns:
        database.execute(
            "ALTER TABLE radio_broadcast_assignments "
            "ADD COLUMN broadcast_message TEXT NOT NULL DEFAULT ''"
        )


def _migrate_mud_settings(database):
    """Add the native MUD feature switch without changing an operator's data."""
    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(station)")
    }
    if "mud_enabled" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN mud_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK(mud_enabled IN (0,1))"
        )
    if "mud_traditional_enabled" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN mud_traditional_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK(mud_traditional_enabled IN (0,1))"
        )
        database.execute(
            "UPDATE station SET mud_traditional_enabled=mud_enabled"
        )
    if "mud_procedural_enabled" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN mud_procedural_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK(mud_procedural_enabled IN (0,1))"
        )


def _migrate_mud_crossover(database):
    """Add reversible possession provenance and preserve the legacy shared loadout."""
    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(mud_item_instances)")
    }
    if "possession_mode" not in columns:
        database.execute(
            "ALTER TABLE mud_item_instances ADD COLUMN possession_mode TEXT NOT NULL "
            "DEFAULT 'traditional' CHECK(possession_mode IN ('traditional','procedural'))"
        )
        # Dungeon pickups have an unambiguous source. Managed theme rewards are
        # procedural too; traditional rewards keep the conservative legacy default.
        database.execute(
            "UPDATE mud_item_instances SET possession_mode='procedural' "
            "WHERE id LIKE 'expedition:item:%' OR definition_id IN ("
            "SELECT definition_id FROM mud_dungeon_managed_definitions)"
        )
    # Chunk 6 stored one global equipped slot on the item. Retain that exact
    # loadout as the shared loadout, then clear the compatibility column so the
    # new per-mode loadouts can coexist without violating its legacy index.
    database.execute(
        "INSERT OR IGNORE INTO mud_character_equipment"
        "(character_id,loadout_key,slot,item_instance_id) "
        "SELECT owner_character_id,'shared',equipped_slot,id FROM mud_item_instances "
        "WHERE owner_character_id IS NOT NULL AND equipped_slot IS NOT NULL"
    )
    database.execute(
        "UPDATE mud_item_instances SET equipped_slot=NULL "
        "WHERE equipped_slot IS NOT NULL"
    )


def _migrate_mud_locations(database):
    """Extend presence for system locations without rebuilding legacy tables."""
    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(mud_presence)")
    }
    if "location_type" not in columns:
        database.execute(
            "ALTER TABLE mud_presence ADD COLUMN location_type TEXT NOT NULL DEFAULT 'world_room'"
        )
    if "location_key" not in columns:
        database.execute("ALTER TABLE mud_presence ADD COLUMN location_key TEXT")
    if "instance_id" not in columns:
        database.execute("ALTER TABLE mud_presence ADD COLUMN instance_id TEXT")
    database.execute(
        "UPDATE mud_presence SET location_key=room_id "
        "WHERE location_type='world_room' AND location_key IS NULL"
    )


def _migrate_mud_dungeon_instances(database):
    """Add reproducibility metadata without altering existing generated records."""
    columns = {
        row["name"] for row in database.execute(
            "PRAGMA table_info(mud_procedural_instances)"
        )
    }
    additions = {
        "theme_id": "TEXT",
        "theme_version": "TEXT",
        "template_hash": "TEXT",
        "generator_version": "TEXT",
        "layout": "TEXT",
        "room_count": "INTEGER",
        "difficulty": "INTEGER",
        "generation_manifest_json": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in columns:
            database.execute(
                f"ALTER TABLE mud_procedural_instances ADD COLUMN {name} {declaration}"
            )


def _migrate_mud_expeditions(database):
    """Add live-run metadata without disturbing Chunk 10 pending designs."""
    columns = {
        row["name"] for row in database.execute(
            "PRAGMA table_info(mud_procedural_instances)"
        )
    }
    additions = {
        "owner_user_id": "INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "join_code": "TEXT",
        "terminal_reason": "TEXT",
        "started_at": "TEXT",
        "ended_at": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in columns:
            database.execute(
                f"ALTER TABLE mud_procedural_instances ADD COLUMN {name} {declaration}"
            )
    database.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mud_expedition_join_code "
        "ON mud_procedural_instances(join_code) WHERE join_code IS NOT NULL"
    )
    combat_columns = {
        row["name"] for row in database.execute(
            "PRAGMA table_info(mud_combat_encounters)"
        )
    }
    if "dungeon_content_id" not in combat_columns:
        database.execute(
            "ALTER TABLE mud_combat_encounters ADD COLUMN dungeon_content_id TEXT"
        )
    database.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mud_combat_live_dungeon_enemy "
        "ON mud_combat_encounters(dungeon_content_id) "
        "WHERE dungeon_content_id IS NOT NULL AND status IN ('ready','active')"
    )

def _migrate_legacy_callsigns(database):
    """Replace callsigns created under earlier, prefixed formats."""
    from .security import generate_user_callsign, valid_callsign

    station = database.execute("SELECT callsign FROM station WHERE id=1").fetchone()
    if station and not valid_callsign(station["callsign"]):
        database.execute(
            "UPDATE station SET callsign=? WHERE id=1",
            (generate_user_callsign(database),),
        )

    users = database.execute("SELECT id,callsign FROM users").fetchall()
    for user in users:
        if not valid_callsign(user["callsign"]):
            database.execute(
                "UPDATE users SET callsign=? WHERE id=?",
                (generate_user_callsign(database), user["id"]),
            )

def get_station():
    return get_db().execute("SELECT * FROM station WHERE id=1").fetchone()

def init_app(app):
    app.teardown_appcontext(close_db)

