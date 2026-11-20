-- Dream Shorts, three per-Short options that default OFF (existing Shorts and the default form are unchanged):
--  hook_title:  a big two-colour title card burned in during the first ~2 s of the Finish. hook_text is the title
--               ("THE STREET *FOLDED* LIKE PAPER": the starred word is the large coloured one); hook_locked = the
--               owner (or the idea pack) set it, so a rewrite by the writer never replaces it.
--  chain_shots: clip N+1 starts from the last frame of clip N (one continuous camera move through one world).
--  idea_ref:    "pack/idea" picked from the idea pack instead of researching (the Scout is skipped).
ALTER TABLE dream_short ADD COLUMN hook_title boolean NOT NULL DEFAULT false;
ALTER TABLE dream_short ADD COLUMN hook_text text;
ALTER TABLE dream_short ADD COLUMN hook_color text;
ALTER TABLE dream_short ADD COLUMN hook_locked boolean NOT NULL DEFAULT false;
ALTER TABLE dream_short ADD COLUMN chain_shots boolean NOT NULL DEFAULT false;
ALTER TABLE dream_short ADD COLUMN idea_ref text;
