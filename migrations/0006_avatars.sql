-- Profile pictures for people and organisations. One image per owner, replaced on each upload.
-- The image is kept in the database so it travels with the rest of the team's data.
CREATE TABLE IF NOT EXISTS avatars (
  owner_kind text NOT NULL CHECK (owner_kind IN ('user', 'team')),
  owner_id text NOT NULL,
  media_type text NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg', 'image/webp', 'image/gif')),
  data bytea NOT NULL,
  updated_by text NOT NULL REFERENCES users,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_kind, owner_id)
);
