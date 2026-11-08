-- The chat list reads each chat's latest Turn and latest reply. These indexes keep that a few
-- index lookups per chat, however long the chats get.
CREATE INDEX IF NOT EXISTS chats_owner ON chats (team_id, user_id);
CREATE INDEX IF NOT EXISTS chat_events_chat_reply ON chat_events (chat_id, id) WHERE kind = 'REPLY';
