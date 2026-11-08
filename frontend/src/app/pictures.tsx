import { useRef } from "react";
import { PICTURE_ACCEPT, setPicture, type PictureOwner } from "../api";
import { refreshAll, useAction } from "../hooks";
import { useApp } from "./context";

/**
 * Changes the profile picture of the signed-in person ("me") or of the current organisation ("team").
 * The file input lives in the shell, outside any menu, so it stays mounted while the file picker is open.
 */
export function usePicture(owner: PictureOwner) {
  const { session, updateSession } = useApp();
  const act = useAction();
  const ref = useRef<HTMLInputElement | null>(null);
  const apply = (url: string | null) => {
    updateSession((s) => owner === "me"
      ? { ...s, user: { ...s.user, avatar_url: url } }
      : { ...s, team: { ...s.team, avatar_url: url }, teams: s.teams.map((t) => (t.id === s.team.id ? { ...t, avatar_url: url } : t)) });
    refreshAll();
  };
  const input = (
    <input ref={ref} type="file" accept={PICTURE_ACCEPT} hidden aria-hidden="true" tabIndex={-1}
      onChange={(e) => {
        const file = e.target.files?.[0];
        e.target.value = "";
        if (file) act.run(async () => apply(await setPicture(owner, file)));
      }} />
  );
  return {
    input,
    current: owner === "me" ? session.user.avatar_url : session.team.avatar_url,
    choose: () => { act.setError(null); ref.current?.click(); },
    remove: () => act.run(async () => apply(await setPicture(owner, null))),
    busy: act.busy,
    error: act.error,
  };
}

export type Picture = ReturnType<typeof usePicture>;
