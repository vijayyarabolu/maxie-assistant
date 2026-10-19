# Maxie.app, the notch app (macOS only, optional)

This folder (`ClaudeNotch`) builds Maxie.app, a small menu-bar app that puts Maxie next to the MacBook notch.
Maxie sits there as a tiny animated character. Click it and a panel opens with the chat, the live 3D bot
(a WKWebView of `web/bot.html`), what Maxie is doing, Allow / Deny cards, today's brief and the budget.
It also answers the hub's questions about Messages, Calendar, Contacts, Notes and Mail on this Mac, and asks
macOS for the permissions those need.

The Python hub does all the thinking. The notch app is optional: Maxie works without it in the browser and on
the phone. You only need it if you want Maxie on your Mac's screen.

## What you need

- macOS 15 or later
- Xcode (the full app, not just the command line tools)
- XcodeGen: `brew install xcodegen`
- The hub set up and running (see the main README)

## Build and run

Run these from the repo root.

```sh
make mascot        # render the notch animation frames first (they are not in the repo)
make notch         # generate the Xcode project, build and sign Maxie.app into build-stage.noindex, verify it
make notch-run     # put the verified build in place and open it
make notch-test    # unit tests, run on their own copy of the app (MaxieTestHost, dev.maxie.notch.tests)
```

`make mascot` runs `scripts/render_mascot.py`, which writes `assets/mascot` (Maxie) and `assets/mascot-classic`
(the Classic robot look). The Xcode project bundles both folders, so the build fails until they exist. Render
them again after you change the mascot.

To work in Xcode instead: `cd mac/ClaudeNotch && xcodegen && open ClaudeNotch.xcodeproj`.

## Signing

`make notch` never builds ad-hoc. An ad-hoc signature changes with every build, and macOS then forgets every
permission you gave Maxie. The first build runs `scripts/make_signing_identity.sh`, which makes a self-signed
identity called "Maxie Local Signing" in its own keychain. Every later build signs with it, so permissions stick.
To use a certificate of your own, set `MAXIE_SIGN_IDENTITY` to the start of its name.

## Connecting to the hub

The app talks to the hub on `http://127.0.0.1:8780`. It reads the hub token from the login Keychain, from the
item with service `maxie` and account `HUB_TOKEN`. You don't create that item by hand: in the hub's web
Settings, go to Access and press "Connect the Mac app", which writes it for you. If the panel says the token
doesn't match, do that step again.

Google accounts, the passcode, devices and app tokens are all managed in the web Settings. The app's own
Settings window shows what is connected and has a "Manage in browser" button for each.

## Notes for development

- The token is read through `/usr/bin/security`, the same way the hub reads it, so no Keychain dialog appears.
- The notch character is drawn with Core Animation (`MascotLayerView`): one frame image swapped 30 times a
  second, plus two eye layers that move. Idle, it costs about 1% CPU. The SwiftUI `MascotView` is kept as a
  fallback.
- Other processes can open or close the panel with a `DistributedNotificationCenter` notification named
  `dev.maxie.notch.panel` and the object `open`, `close` or `toggle` (the voice helper, Shortcuts and the tests
  use it; `scripts/perf/macremote.swift` is a small example).
- `CLAUDE_NOTCH_DEBUG=1` traces show, expand and collapse to stderr. `CLAUDE_NOTCH_SNAPSHOT=<dir>` writes PNGs of
  the mini bot in each state, the panel and the live 3D view, then quits. It draws everything off-screen, so it
  needs no Screen Recording permission. For either one, run the binary inside the .app directly.
- Logs go to `~/Library/Logs/maxie`.
