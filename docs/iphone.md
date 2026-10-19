# Maxie on the iPhone

The phone talks to the hub on your computer. It needs a private HTTPS address for it, because iOS wants
HTTPS for the home-screen app and you should never put the hub on the open internet. I use Tailscale:
install it on the computer and the phone, sign both in, then on the computer run

```sh
tailscale serve --bg --https=8780 http://127.0.0.1:8780
```

The hub is then at `https://<computer-name>.<your-tailnet>.ts.net:8780`, reachable only by your own devices.
Below, *HUB* means that address.

The Shortcut needs an app token: in Settings, Access, App tokens, type "iPhone Shortcut" and press **Make a
token**. Copy it right away; it's shown once. Below, *token* means that value. You can revoke it from the
same list any time.

## 1. The "Hey Max" Shortcut (Action Button + hands-free)

One Shortcut does it all: it listens, asks the hub (`POST /api/ask`), speaks the short answer, opens the
app when there is more on screen, and listens again for a follow-up. Build it once, tap by tap:

1. Open **Shortcuts** → **+** → tap the name at the top → **Hey Max**.
2. Add **Dictate Text**. Tap the arrow on it: Language **English**, Stop Listening **After Pause**.
3. Add **Get Contents of URL**. Tap the arrow:
   - URL: *HUB*`/api/ask`
   - Method **POST**
   - Headers: **+** → Key `Authorization`, Value `Bearer ` followed by your *token*
   - Request Body **JSON**: **+** → Key `text`, Type **Text**, Value: pick the **Dictated Text** variable.
4. Add **Get Dictionary Value** → Key `reply` → from **Contents of URL**.
5. Add **Speak Text** → pick the **Dictionary Value** from step 4. Tap the arrow: **Wait Until Finished** on.
6. Add **Get Dictionary Value** → Key `more_on_screen` → from **Contents of URL**.
7. Add **If** → **Dictionary Value** (step 6) **is** `true` → inside the If, add **Open App** → **Maxie**
   (the home-screen app from section 2; it shows the whole answer). Leave *Otherwise* empty.
8. Follow-ups without saying "Hey Max" again: add **Repeat** → **3** times, and move steps 2 to 7 inside it.
   After step 5 inside the loop add **If** → **Dictated Text** **has any value** … **Otherwise** → **Stop This
   Shortcut**. Silence after the answer (one pause, about 6 s) ends the conversation.
9. **Phone actions** (the phone does these itself; the hub only decides). After step 3 add **Get Dictionary
   Value** → Key `phone_action` → **If** it **has any value**, then **Get Dictionary Value** `action` and
   `args` from it, and add one **If** per action (Otherwise → nothing):

   | `action` | Shortcut step | uses from `args` | say, for example |
   |---|---|---|---|
   | `timer` | **Start Timer** | `minutes` (Minutes) | "set a 10 minute timer" |
   | `music` | **Play Music** / **Pause** / **Skip Forward** / **Skip Back** | `command`, `query` | "play some jazz", "pause" |
   | `open_app` | **Open App** (choose with **Text** → `name`) | `name` | "open Spotify" |
   | `open_url` | **Open URLs** | `url` (Maps links open Apple Maps) | "directions to Costco", "open youtube" |
   | `add_reminder` | **Add New Reminder**, Title = `text`; Alert = **Get Dates from** `when` (skip if empty) | `text`, `when` | "remind me to call Mom at 6 pm" |
   | `draft_message` | **Send Message**, Recipients = `to`, Message = `text`, **Show When Run** ON (you send it) | `to`, `text` | "text Jordan I'm on my way" |

   Maxie can also pick these itself on a phone turn ("time my pasta, about twelve minutes"); they come
   back the same way. Nothing is sent without you: a message always opens as a draft.
10. Tap **Done**. Run it once from the Shortcuts app and allow the two prompts (speech recognition, the
    network request to the hub's address).

**Action Button** (iPhone 15 Pro and later): **Settings → Action Button** → swipe to **Shortcut** → **Choose a
Shortcut…** → **Hey Max**. Press and hold the button: it listens straight away.

**Hands-free "Hey Max"**: **Settings → Accessibility → Vocal Shortcuts → Set Up Vocal Shortcut** → choose
**Hey Max** under Shortcuts → say "Hey Max" three times → **Continue**. From then on, saying "Hey Max"
runs it even with the phone locked (the computer running the hub has to be awake). Backup: "Hey Siri, Hey Max".

How you know it worked: say "what time is it"; the phone speaks the answer within about a second.

## 2. The home-screen app (PWA)

1. In Safari on the phone open *HUB* and sign in with your passcode.
2. **Share, Add to Home Screen**, then open it from the Home Screen icon.
3. In its Settings tab, tap **Turn on notifications** (iOS 16.4 and later, only from the Home Screen icon).
   Then press **Test** next to Phone notifications on the Settings page.
4. To talk in the app, hold the mic. Where Safari doesn't allow speech recognition, use the keyboard's mic.

The status line always shows a word, not only a colour: **Here**, **Listening**, **Thinking**, **Needs your
OK**, **Can't reach the hub**, **Muted**. Anything risky (sending mail, invites) waits for your tap.

## What `/api/ask` returns

```json
{"reply": "It's 9:41 AM.", "full": "It's 9:41 AM.", "more_on_screen": false,
 "confirmations": [], "phone_action": null, "first_ms": 12, "total_ms": 14}
```

`reply` is at most three short sentences (ending in "More on screen." when `full` is longer); `full` is the
whole answer and is also in the app's chat history. `confirmations` lists anything waiting for your OK.
