-- Read state is per person. Notifications are mostly sent to every Approver in a team,
-- so one read_at column on the notification would mark it read for everyone.
CREATE TABLE notification_reads (
  notification_id text NOT NULL REFERENCES notifications,
  user_id text NOT NULL REFERENCES users,
  read_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (notification_id, user_id)
);
