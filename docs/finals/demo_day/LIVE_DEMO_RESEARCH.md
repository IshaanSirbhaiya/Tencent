# Live demo research: making a 5-minute demo survive the stage

Walnut DataBridge, Demo Day, Tue 3 Nov, Marina Bay Sands. 5 min pitch + 1 min Q&A, cold start.
What other people learned about live demos, and what we change in our kit because of it.
Every outside claim has a link. Anything marked "(our practice)" is our own rule, not a sourced fact.
Researched 30 Oct 2026.

## 1. Why demo live at all, and what can go wrong

- YC's own Demo Day guide says live demos "seldom work" in short pitches, but also says to avoid videos because investors want to see you perform live [https://www.ycombinator.com/blog/guide-to-demo-day-pitches/].
- Paul Graham: show the product solving the problem early, do not walk through a feature list, and assume the network "will mysteriously die 30 seconds into your presentation"; keep a copy of the server running on your laptop [https://paulgraham.com/investors.html].
- Judges at hackathons watch the demo, not the code; prepare for "what is mocked versus real" in Q&A [https://angelhack.com/blog/10-tips-to-help-you-rock-your-next-hackathon-demo/].

What we do because of this: we demo live by default (the judges' favourite), but on a local stack with the network used only for the model call, and we can say in one sentence what is live and what is cached.

## 2. The golden path and pre-warmed state

- The 2007 iPhone demo worked because engineers found a fixed order of steps that did not crash (a "golden path"), rehearsed it for days, and controlled the network: the demo Wi-Fi was hidden from the audience [https://9to5mac.com/2017/01/09/steve-jobs-iphone-presentation-2007-inside-story/].
- Pick demo outcomes that are repeatable, not ones that need perfect conditions [https://www.red-gate.com/simple-talk/career/how-to-avoid-demo-fails-when-presenting-at-tech-conferences/].
- Preload or cache slow steps so there is no dead air; seed demo data; bookmark the URL rather than typing it [https://angelhack.com/blog/10-tips-to-help-you-rock-your-next-hackathon-demo/].
- Warm the machine up well before presenting (one commenter runs the host for about an hour first), and do not install updates or change extensions right before a talk [https://haacked.com/archive/2011/04/18/presentation-tips.aspx/].

What we do because of this: the one-click Start boots every service, then the preflight page at /present/preflight must show all green (services up, model reachable, clips present) before we walk on. P runs only the golden path; nothing on stage is typed. Start the stack at least 15 minutes before our slot (our practice).

## 3. A clean, app-mode browser profile

- `--user-data-dir` points Chrome at a separate profile folder; the default on Windows is `%LOCALAPPDATA%\Google\Chrome\User Data`, and two running instances cannot share one folder [https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md].
- On Windows, quit any running Chrome before adding switches, and check `chrome://version` to see the command line actually in use [https://www.chromium.org/developers/how-tos/run-chromium-with-flags/].
- Switch meanings, from the Chromium switch list [https://peter.sh/experiments/chromium-command-line-switches/]:
  - `--app=URL`: launch the URL in "application" mode (no tabs or address bar).
  - `--kiosk`: kiosk mode (full screen, no browser UI).
  - `--no-first-run`: skip first-run tasks and extra dialogs, prompts and bubbles.
  - `--no-default-browser-check`: no "make me default" info-bar.
  - `--disable-extensions`, `--disable-notifications` (disables web notifications and push).
  - `--window-size=1920,1080`, `--window-position=0,0`, `--start-fullscreen` (as if F11 was pressed).
  - `--force-device-scale-factor=1`: overrides the scale factor for browser UI and page contents.
- Edge supports the same idea: `msedge.exe --kiosk URL --edge-kiosk-type=fullscreen --no-first-run`. Edge kiosk runs InPrivate and blocks F11/F12 [https://learn.microsoft.com/deployedge/microsoft-edge-configure-kiosk-mode].
- A separate demo-only user with big fonts and nothing personal on it is an old presenter habit [https://www.hanselman.com/blog/11-top-tips-for-a-successful-technical-presentation].

What we do because of this: the launcher opens Chrome (Edge as fallback) with `--user-data-dir=databridge/var/demo-browser --app=http://127.0.0.1:3100/... --no-first-run --no-default-browser-check --disable-extensions --disable-notifications --window-position=0,0 --window-size=1920,1080 --force-device-scale-factor=1`. We use `--app`, not `--kiosk`, so we can still Alt+Tab to the clip player or slides if needed (our practice). Because the profile folder is separate, the flags apply even if a personal Chrome is open; still close personal Chrome so no tabs or pop-ups leak (our practice).

## 4. Silence Windows 11: notifications, Focus, updates

- Do not disturb by hand: Win+N, then the bell icon with "zZ" [https://support.microsoft.com/windows/8942c744-6198-fe56-4639-34320cf9444e].
- Do not disturb automatically: Start > Settings > System > Notifications > "Turn on do not disturb automatically", tick "When duplicating the display" and "When using an app in full-screen mode" [same source].
- Per-app: Settings > System > Notifications > "Notifications from apps and other senders" > switch off each app [same source].
- Focus session: Settings > System > Focus > "Start focus session"; it turns on Do not disturb and hides taskbar badges and flashing (needs the Clock app) [https://support.microsoft.com/topic/cbcc9ddb-8164-43fa-8919-b9a2af072382].
- Pause updates: Settings > Windows Update > "Pause updates" > pick a date (up to 35 days). While paused, updates that need a restart are not installed and the PC will not auto-restart; pausing cancels updates in progress [https://support.microsoft.com/help/4028233].
- Mark the hotspot as metered: Settings > Network & internet > Wi-Fi > (the hotspot) > Properties > "Metered connection" on. Windows Update then downloads only priority updates and Store downloads may pause [https://support.microsoft.com/en-us/windows/metered-connections-in-windows-7b33928f-a144-b265-97b6-f2e95a87c408].
- Turn off notifications on phones and watches too, so private messages never reach the screen [https://localghost.dev/blog/things-experienced-speakers-wish-they-d-known/].

What we do because of this: the night before, pause updates (pick a date after 3 Nov), mark the hotspot metered, turn on DND, and quit chat apps, OneDrive and anything with pop-ups. The preflight page shows a manual tick-box for each (our practice).

## 5. Power: plan, sleep, screen, lid

- Timeouts: Settings > System > Power & battery > "Screen, sleep, & hibernate timeouts" > "Turn my screen off after" and "Make my device sleep after", for Plugged in and On battery [https://support.microsoft.com/topic/0d6a2b6b-2e87-4611-9980-ac9ea2175734].
- Power mode is on the same page (Settings > System > Power & battery > Power mode) [same source].
- Lid: Settings > System > Power & battery > "Lid & power button controls" > "Closing the lid will make my PC" (24H2+); or Control Panel > Hardware and Sound > Power Options > "Choose what closing the lid does" [https://pureinfotech.com/change-close-lid-action-windows-11/].
- Plug in the laptop and disable sleep before the slot [https://angelhack.com/blog/10-tips-to-help-you-rock-your-next-hackathon-demo/]; bring spare power [https://lornajane.net/posts/2017/live-demo-risks-and-rewards].

What we do because of this: Plugged in: screen off = Never, sleep = Never; power mode = Best performance; lid (plugged in) = Do nothing, so a bumped lid does not kill the demo. Charger in the bag, laptop at 100% before we queue (our practice).

## 6. Display: Duplicate at 1920x1080, scaling 100%

- Win+P offers PC screen only, Duplicate, Extend, Second screen only; Duplicate shows the same content on all displays [https://support.microsoft.com/article/7e170c39-58dc-c866-7d55-be2372632892].
- Resolution and scale: Start > Settings > System > Display > pick the display > "Scale & layout" > Scale, and "Display resolution" [https://support.microsoft.com/help/3025083].
- Set the laptop to the projector's native resolution to avoid blurry text, and test the projector early [https://haacked.com/archive/2011/04/18/presentation-tips.aspx/].
- Practise with the mirrored screen setup you will actually use [https://lornajane.net/posts/2017/live-demo-risks-and-rewards].

What we do because of this: Duplicate, not Extend, so what we see is what the room sees and no window lands on the wrong screen. Both screens 1920x1080, scale 100%, browser zoom 100% (Ctrl+0). If the venue screen is not 1080p, keep Duplicate and accept the venue's resolution; the app is built to read at 1920x1080 (our practice).

## 7. Legible from the back of the room

- Fonts "large and easily legible from a distance", high contrast, important text at the top because the back rows may not see the bottom of the screen [https://www.ycombinator.com/blog/guide-to-demo-day-pitches/].
- Room size and projector change what is readable; use a simple, high-contrast theme and check it in the room if possible [https://localghost.dev/blog/things-experienced-speakers-wish-they-d-known/].
- Use a zoom tool (Sysinternals ZoomIt) for small details, and practise with it [https://www.hanselman.com/blog/11-top-tips-for-a-successful-technical-presentation].

What we do because of this: back-of-room test: put the demo on a TV or projector, stand 8-10 m away (or view the laptop from about 3 m), and read every step's headline number. If a label needs squinting, make it bigger in the app, not in zoom (our practice). Key numbers sit in the top half of the screen.

## 8. Cables, adapters and HDMI/USB-C

- "Display connection might be limited" usually means the wrong USB-C port, a hub or dock in the way, or a cable/adapter without DisplayPort alternate mode; plug the adapter straight into a port that supports it [https://support.microsoft.com/en-au/windows/fix-usb-c-problems-in-windows-f4e0e529-74f5-cdae-3194-43743f30eed2].
- Bring your own labelled adapter; do not assume the organisers have one [https://localghost.dev/blog/things-experienced-speakers-wish-they-d-known/].

What we do because of this: carry two video paths (laptop HDMI port plus a tested USB-C to HDMI adapter), one spare HDMI cable, and the charger. Test each with Win+P > Duplicate at the tech check (our practice).

## 9. Offline-first, phone hotspot, backup clips

- Run services locally so the demo does not need venue Wi-Fi; carry a mobile hotspot as the connectivity backup [https://lornajane.net/posts/2017/live-demo-risks-and-rewards].
- Test the talk in airplane mode; do not trust the Wi-Fi [https://localghost.dev/blog/things-experienced-speakers-wish-they-d-known/].
- Swap local fixtures in for network calls; record a clean run the night before and keep it local [https://angelhack.com/blog/10-tips-to-help-you-rock-your-next-hackathon-demo/].
- Plan in tiers: A live, B "clearly labelled videos" of each demo, C slides and videos on a USB stick [https://lornajane.net/posts/2017/live-demo-risks-and-rewards].
- Narrate the backup video live as if doing the demo, and do not debug on stage unless the fix is trivial [https://www.red-gate.com/simple-talk/career/how-to-avoid-demo-fails-when-presenting-at-tech-conferences/].
- Judges know tech breaks; a calm switch costs less than the failure itself (our practice, consistent with the sources above).

What we do because of this: the hotspot carries only the TokenHub model call. The ladder is live -> R reset -> offline mock build on port 3101 -> B plays the local clip for the current step from public/clips -> slides. Target switch time: under 5 seconds for B, under 15 seconds for the mock (our practice). Rehearse each switch at least twice with a stopwatch.

## 10. Reset path and recovery keys

- Have a "one-click" way to reset every demo [https://www.hanselman.com/blog/11-top-tips-for-a-successful-technical-presentation].
- Keep the environment easy to rebuild and start from a clean test database [https://lornajane.net/posts/2017/live-demo-risks-and-rewards].
- Recovery during the talk is fine if you explain it calmly [https://www.hanselman.com/blog/11-top-tips-for-a-successful-technical-presentation].

What we do because of this: R resets to step 0 without reloading services; P starts; Space advances; B plays the clip. Write the four keys on a card taped below the trackpad (our practice). One spoken line ready for any failure: "the venue network is slow, here is the same step recorded this morning" (our practice).

## 11. Rehearsal

- Practise, then practise more; record yourself if no one can watch [https://www.ycombinator.com/blog/guide-to-demo-day-pitches/].
- Practise out loud, timed, with an outsider; the first run shows what to cut [https://angelhack.com/blog/10-tips-to-help-you-rock-your-next-hackathon-demo/].
- Write a timed script with milestones so you know if you are behind [https://haacked.com/archive/2011/04/18/presentation-tips.aspx/].

What we do because of this: at least three full timed cold-start runs on the demo laptop (reboot, Start, preflight, pitch), one of them with the hotspot off to force the fallback ladder (our practice).

## Sources

All opened during this research on 30 Oct 2026.

1. Y Combinator, A Guide to Demo Day Presentations: https://www.ycombinator.com/blog/guide-to-demo-day-pitches/
2. Paul Graham, How to Present to Investors: https://paulgraham.com/investors.html
3. Chromium, Run Chromium with command-line switches: https://www.chromium.org/developers/how-tos/run-chromium-with-flags/
4. Chromium docs, User Data Directory: https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md
5. Peter Beverloo, List of Chromium Command Line Switches: https://peter.sh/experiments/chromium-command-line-switches/
6. Microsoft Learn, Configure Microsoft Edge kiosk mode: https://learn.microsoft.com/deployedge/microsoft-edge-configure-kiosk-mode
7. Microsoft Support, Notifications and Do Not Disturb in Windows: https://support.microsoft.com/windows/8942c744-6198-fe56-4639-34320cf9444e
8. Microsoft Support, Focus: Stay on Task Without Distractions in Windows: https://support.microsoft.com/topic/cbcc9ddb-8164-43fa-8919-b9a2af072382
9. Microsoft Support, Pause updates in Windows: https://support.microsoft.com/help/4028233
10. Microsoft Support, Metered connections in Windows: https://support.microsoft.com/en-us/windows/metered-connections-in-windows-7b33928f-a144-b265-97b6-f2e95a87c408
11. Microsoft Support, Power settings in Windows 11: https://support.microsoft.com/topic/0d6a2b6b-2e87-4611-9980-ac9ea2175734
12. Pureinfotech, How to change lid close action on Windows 11: https://pureinfotech.com/change-close-lid-action-windows-11/
13. Microsoft Support, Screen mirroring and projecting (Win+P modes): https://support.microsoft.com/article/7e170c39-58dc-c866-7d55-be2372632892
14. Microsoft Support, Change your screen resolution and layout in Windows: https://support.microsoft.com/help/3025083
15. Microsoft Support, Fix USB-C problems in Windows: https://support.microsoft.com/en-au/windows/fix-usb-c-problems-in-windows-f4e0e529-74f5-cdae-3194-43743f30eed2
16. 9to5Mac, inside story of the 2007 iPhone presentation: https://9to5mac.com/2017/01/09/steve-jobs-iphone-presentation-2007-inside-story/
17. Redgate Simple Talk, How to avoid demo fails at tech conferences: https://www.red-gate.com/simple-talk/career/how-to-avoid-demo-fails-when-presenting-at-tech-conferences/
18. Lorna Mitchell, Live Demo: Risks and Rewards: https://lornajane.net/posts/2017/live-demo-risks-and-rewards
19. localghost.dev, Things experienced speakers wish they'd known: https://localghost.dev/blog/things-experienced-speakers-wish-they-d-known/
20. Scott Hanselman, 11 Top Tips for a Successful Technical Presentation: https://www.hanselman.com/blog/11-top-tips-for-a-successful-technical-presentation
21. Phil Haack, Presentation Tips Learned From My (Many) Mistakes: https://haacked.com/archive/2011/04/18/presentation-tips.aspx/
22. AngelHack, 10 tips to help you rock your next hackathon demo: https://angelhack.com/blog/10-tips-to-help-you-rock-your-next-hackathon-demo/

Not found: no first-hand Stripe Sessions or Vercel Ship write-up on how their keynote demos are rehearsed turned up in searches, so none is cited.
