-- A pasted Gemini key is now kept only in the agent worker's memory. This removes the table an
-- earlier version stored it in, and any key saved there.
DROP TABLE IF EXISTS gemini_keys;
