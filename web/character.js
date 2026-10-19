// Which bot to draw: Maxie (Mochi, no headphones) by default; the Classic robot
// only as the optional "Classic" look. Same API either way (setState, setLook, start, stop, dispose, ...).
//   MaxieBots.create(canvas, { character: "maxie" | "classic", look: {fur, furLength, ears, eyes, accessory}, ...opts })
//   MaxieBots.choices()  ->  [{value: "mochi", name: "Mochi periwinkle"}, ..., {value: "classic", name: "Classic robot"}]
(function (root) {
  "use strict";

  // States only Maxie has; the Classic robot borrows a close one (the Mac does the same, MascotLibrary.fallback).
  const CLASSIC_FALLBACK = { wave: "happy", closed: "sleep", stretch: "happy" };

  function create(canvas, cfg = {}) {
    const { character = "maxie", look = {}, ...opts } = cfg;
    if (character === "classic" || !root.MaxieCharacter) {
      const classic = root.ClaudeMascot.create(canvas, { ...opts, look: typeof look === "string" ? look : "sunset" });
      const bot = Object.create(classic);  // keeps its getters; only setState is wrapped
      bot.setState = (s, ...rest) => classic.setState(CLASSIC_FALLBACK[s] || s, ...rest);
      return bot;
    }
    return root.MaxieCharacter.create(canvas, { ...opts, custom: look });
  }

  function choices() {
    const furs = Object.entries(root.MaxieCharacter?.LOOKS || {}).map(([value, v]) => ({ value, name: v.name }));
    return [...furs, { value: "classic", name: "Classic robot" }];
  }

  root.MaxieBots = { create, choices };
})(typeof window !== "undefined" ? window : globalThis);
