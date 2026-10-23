# AndroidConnect for Noctalia v5

Version **2.3.0** uses upstream Noctalia's `ui.pixelStream` and content-sized
panel height. It requires **plugin API 42 or newer**, including
`height = "content"` with `max_height`. The upstream source tested is commit
`6423cc232e5b0900f28852b97f50d2836f4d39b7`.

No custom shell patch is needed. Your installed Noctalia build must include these
APIs; its v5 version label alone does not establish that. The previously published
v2.2.0 release uses the old `ui.frame` patch and is not this migration.
See upstream's [Pixel streams documentation](https://github.com/noctalia-dev/noctalia/blob/main/docs/user/plugins/development/declarative-ui.mdx#pixel-streams).

The [v4 QML plugin](https://github.com/demencia89/noctalia-shell-androidconnect-plugin)
remains in its own repository, with independent releases and update workflow.

## Install from GitHub

Run these in a compatible upstream Noctalia session:

```sh
noctalia msg plugins source add androidconnect-v5 git https://github.com/demencia89/noctalia-androidconnect-v5
noctalia msg plugins enable demencia89/androidconnect
noctalia msg panel-open demencia89/androidconnect:panel
```

To update later, run `noctalia msg plugins update androidconnect-v5`.
If you already use the local development port, keep that source until you choose
to switch; two sources with the same plugin ID do not run as separate copies.

## Install locally

For local development, clone this repository and use a path source instead of
the Git source above, replacing the source path:

```sh
noctalia msg plugins source add androidconnect-v5-dev path /absolute/path/to/noctalia-androidconnect-v5
noctalia msg plugins enable demencia89/androidconnect
noctalia msg panel-open demencia89/androidconnect:panel
```

Add **AndroidConnect → bar** from the bar widget picker and **AndroidConnect →
toggle** from the control-center shortcut picker. The panel can also be opened
from a keybind with:

```sh
noctalia msg panel-toggle demencia89/androidconnect:panel
```

The replacement for the v4 IPC toggle is:

```sh
noctalia msg plugin demencia89/androidconnect:devices all toggle
```

Disable with `noctalia msg plugins disable demencia89/androidconnect`. Stop a
recording and close the panel first so media can finish downloading.

## Dependencies and setup

- Linux and `python3` run the companion; no Python packages are required. The
  transport uses standard-library `memfd_create`, `mmap`, and `SOCK_SEQPACKET`.
- Install the **Adwaita Sans** font for the v4 typography. Fontconfig substitutes
  another font if it is unavailable, which can change text widths and spacing.
- KDE Connect status/actions need a running `kdeconnectd` and `busctl`.
  KDE Connect is **optional** for ADB controls and mirroring.
- Preview/control needs `adb`, `scrcpy`, `ffmpeg`, and `fuser` (usually `psmisc`).
- The preview uses an existing V4L2 loopback device, `/dev/video10` by default.
  Set `video_device` in the plugin's native settings if yours differs. It must
  be readable/writable by your user and reserved for this plugin. The companion
  refuses an occupied device; it never kills another mirroring session, loads a
  kernel module, or runs sudo.
- The header's **Open in window** action only needs `adb` and `scrcpy`; it does
  not need the loopback device or FFmpeg.
- File browsing needs the KDE Connect SFTP plugin and its normal SSHFS/FUSE
  dependencies. `xdg-open` opens the mounted directory.
- QR pairing additionally needs `qrencode` and working `adb mdns services`.

Create/configure the loopback device before starting the preview. If the module
is not already loaded, an example one-session setup is:

```sh
sudo modprobe v4l2loopback video_nr=10 card_label="scrcpy-panel" exclusive_caps=0
```

If you already use virtual cameras, configure the additional device without
unloading a module they are using, or choose an existing unused device in the
plugin settings. Noctalia does not run the command above for you. The v5 renderer
uses FFmpeg rather than Qt Multimedia, so it does not depend on Qt's
device-enumeration cache or a Qt Multimedia package.

For USB: enable USB debugging on Android, connect the phone, and accept the
computer's authorization prompt. For Wi-Fi: enable Wireless debugging on the
phone and use **Wireless** in the panel. Pairing and connect ports are different.
The port does not attempt to enable debugging through KDE Connect.

Choose the KDE Connect phone; its ADB target is matched automatically by its
reachable addresses, including mDNS-named ADB transports. Duplicate IP/mDNS
transports for the same phone do not require a chooser. Old ports no longer pin
the plugin to a disconnected endpoint. Explicit USB associations remain valid;
it never guesses USB identity from a device name. KDE Connect is still optional:
ADB-only mode selects a single authorized phone automatically and shows a chooser
only for multiple targets or an unavailable saved target.

Opening the panel starts a bounded 12-second wireless reconnect attempt when the
selected phone has no ADB connection. Opening **Wireless** prefills that phone's
host, clears volatile pairing/connection ports, and retries for up to 18 seconds.
**Find port** retries manually. Discovery uses ADB mDNS and optional `avahi-browse`,
only for the selected/saved host's advertised TLS connect services; it does not
scan network ports. Changed ports are rediscovered and successful connections
are associated with that phone. Pairing by code also starts connect-port discovery.
Close/switch cancels pending attempts, and stale results cannot retarget another
phone. Discovery runs separately from input handling and does not spam retries
after its timeout. Android Wireless debugging and initial ADB pairing/authorization
are still required; KDE Connect cannot enable them.

## Controls

- Live preview, left-button taps/swipes, and Back/Home/Recents. Embedded
  scrcpy uses `--no-window`, including when audio is enabled, so it does not
  leave an icon-only control window behind the panel. Startup is confirmed by
  decoded frames, not scrcpy's potentially buffered log messages.
- The default video limit is 60 fps. FFmpeg requests that capture rate from the
  reserved V4L2 device and passes captured timestamps through without an extra
  frame-rate conversion filter. The companion passes a sealed shared-memory
  buffer descriptor, not pixel bytes, over the local socket. Noctalia requests
  the next frame after uploading the previous one. The companion keeps only the
  latest captured frame, never modifies a buffer still owned by the shell, and
  tolerates paused drawing without an idle disconnect or a video backlog.
- **Open in window** (external-window icon in the header) stops the embedded preview
  and opens an ordinary scrcpy window for the selected ADB target. The panel
  closes after spawning succeeds; launch errors are reported. The window keeps
  running when the panel closes. Reopening the panel returns to embedded mode;
  disabling the plugin also stops its owned window. Audio and power options are
  carried over, without enabling automatic clipboard synchronization.
- Click the live phone preview to type directly with the PC keyboard. Letters,
  numbers, punctuation, Shift/Caps Lock, Enter, Backspace/Delete, arrows, Home/End,
  and Page Up/Down are forwarded. Ctrl+V pastes text while the phone preview has
  focus. Clicking a panel text field returns keyboard input to that field;
  Escape closes the panel and Tab navigates its native controls. Short text
  bursts are batched to avoid starting one ADB process per character.
- The keyboard drawer still offers an explicit text field and Send text action.
  Open it from **More** or the information drawer; the extra stop/text/wake button row is
  removed from the status view. Closing the panel stops the preview.
- Right-click the phone preview for **Back**, or click the wheel for **Home**.
  Left-click/drag still taps/swipes. Wheel-click deliberately maps to Home as
  requested, rather than v4's Recents action; the Recents button is unchanged.
- Hover the brand logo to reveal its resize icon, then click to cycle
  Small (60%) → Medium (75%) → Large (100%) → Medium → Small, as in v4.
  The phone and panel height resize without restarting the mirror. The selected size
  and cycling direction are saved. Large is the initial default; any saved size
  takes precedence on subsequent opens/restarts. The preview-height setting defines Large.
  Navigation buttons use 32px for both Small and Medium, and the unchanged 42px
  for Large. Button centers stay at 18%, 50%, and 82% of the preview width, so
  spacing and side insets scale proportionally while Home stays centered.
  The main view fits its preview/navigation height with the same 21px outer
  inset as the sides; setup drawers retain extra height when needed for their controls.
  Upstream removed `panel.setSize`; the panel therefore keeps a fixed 680px
  width at every preset, while `height = "content"` fits its rendered height
  up to 900px and the output's available size. Dynamic panel width would require
  a separate upstream extension. The header and action grid fill the available width.
- Hover the live phone preview and use the mouse wheel to scroll. Wheel/tilt
  steps become short vertical/horizontal ADB swipes near the pointer, with
  endpoints kept clear of the system bars. Rapid steps are combined and bounded;
  pending scrolling waits while the companion is busy and is discarded on
  close, disconnect, or target change. Other panel controls keep normal scrolling.
- Optional audio, keep-awake, and screen-off controls. Changing these restarts
  only the owned scrcpy session. scrcpy owns/restores its screen timeout/power
  settings; closing the panel stops that session. Audio is enabled initially;
  the header's audio button can mute it.
- Screenshot, recording, keep-awake, and screen-off form a labeled 2×2 quick-action
  grid beside the phone. Cards, circular icon backplates, and status badges scale
  with the three panel presets. All surfaces, text, outlines, and accents use the
  current Noctalia palette; hover and active states use translucent primary tints.
  Quick-action cards share the actual available width, including inside a scroll
  viewport, so right-hand cards never rely on a fixed sidebar width. The status
  area scrolls when showing an error, keeping longer error text
  accessible without changing the phone geometry. A quiet footer shows the live
  preview/audio state. Startup text and the manual Start preview button are not displayed;
  actual errors remain visible. Reopen the panel to retry automatic preview.
- Screenshot and start/stop recording. Screenshots go to the XDG Pictures
  directory's `AndroidConnect` folder, recordings to XDG Videos/`AndroidConnect`.
  Save notifications report the output path. Failed recording downloads keep
  the uniquely named remote file for recovery rather than deleting it.
- KDE Connect device status, battery/charging, network, signal, notification count,
  pairing/unpairing, ringing, file browsing, and sending a local file by path.
  Unpairing requires a second confirmation click. Battery percentage uses the theme's
  error color below 20%, primary accent above 95%, and normal text otherwise
  (also when unknown). Charging is labeled separately.
- The quieter header keeps Open in window, Audio, Wireless, and **More** visible.
  **More** contains device selection/pairing, file browsing/sharing, Find phone,
  text input, diagnostics, and settings. Wireless and send-file controls have
  their own drawers. These replace the status column while open,
  leaving the phone preview and navigation in place.
- Device names come from KDE Connect when selected, otherwise the ADB-reported
  model. Click the header name to save a local nickname (up to 80 printable
  characters); Reset name or an empty name restores the original. Nicknames are
  saved per KDE Connect identity or ADB serial, and appear in device selectors
  and the bar tooltip. They do not rename Android or KDE Connect, alter target
  selection, or change the brand icon. A nickname tied to a wireless ADB endpoint
  does not automatically follow a different port or USB serial.
- Wireless pairing by code or QR, connection by port, and connect-port discovery
  restricted to the host you entered. Pairing codes/QR secrets are not saved in
  preferences or passed to `adb` through process arguments.

## Current differences from v4

- The main layout retains v4's framed phone on the left and three circular
  navigation buttons, with a redesigned brand/device header, compact device
  status, and labeled quick actions on the right. It uses only Noctalia palette
  roles, including live primary-tinted hover/active states, so dark/light theme
  and palette changes do not need plugin-specific colors. It reuses the v4 brand
  SVGs and cropped phone
  artwork, fitted around the live screen using the original bezel proportions.
  The static phone image uses dark glossy glass instead of a bright wallpaper;
  its bezel and camera cutout are unchanged. It remains visible while disconnected,
  starting, or in an error state, even without the companion/video dependencies. Live video replaces
  it when ready, using the same dimensions at every panel size.
  The existing FFmpeg companion prepares the artwork and antialiased screen mask
  once, then composites the video without another process, frame queue, or shell
  API change. Outer corners remain transparent; the old wallpaper is cut out of
  the screen opening. The whole phone scales together at all three sizes while
  retaining the preview-height setting. Pointer coordinates exclude the bezel;
  bezel clicks/scrolls are ignored and drags beyond the screen are clamped.
  Typography uses v4's Adwaita Sans family and medium/
  bold weights, with Qt point sizes converted to native logical pixels. The
  21px outer inset and adjusted navigation sizes remain unchanged. The header
  has no outline; circular controls and cards use soft, low-opacity borders.
  There is a 14px gap below the header and above navigation, and 24px beside
  the preview. Landscape video is width-limited to leave room for the controls.
  Qt/Pango rasterization and shell-owned blur/shadows
  can still differ; colors continue to follow the active Noctalia theme.
- Keep the phone in its **natural orientation** (normally portrait). The preview
  is locked to that orientation, and touch input verifies orientation instead
  of knowingly forwarding rotated coordinates. Arbitrary live rotation needs
  further work.
- Wheel scrolling is emulated with short swipes, not native Android
  wheel events. Escape remains Noctalia's panel-close key.
- Direct typing and Send text use Android's `input text`, which limits this path
  to printable ASCII; a literal `%s`
  sequence is rejected because Android treats it as a space escape.
- Size presets resize immediately rather than reproducing v4's QML animation.
  The shell owns the fixed width and content-height limit. Capture resolution changes
  from the preview-height setting take effect on the next preview start.
- The initial v5 translation is English. Existing v4 translations are intact.
- Existing v4 `settings.json` is not automatically migrated. The v5 plugin keeps
  its own selections under Noctalia's per-plugin data directory.
- Normal panel close stops media/preview and cleans private sockets/commands.
  A crash or forced kill cannot guarantee finalizing a recording or executing
  Android-side cleanup; stop recordings before disabling/reloading the plugin.

## Implementation and security boundaries

`service.luau` runs one companion; `panel.luau`, `bar.luau`, and `shortcut.luau`
consume plugin-local shared status. `backend.py` handles fixed device actions,
and `video.py` supplies upstream's pixel-stream protocol version 1. There is no toolkit,
codec, or Android logic added to the shell by the port.

The companion creates a unique mode-0700 directory under a verified private
`XDG_RUNTIME_DIR`. Luau submits bounded, atomically renamed command files there;
they are consumed/unlinked, not retained as settings. Socket mode is 0600, peers
must have the same effective UID, and only the latest frame is retained. Device
commands use direct argv; Android shell text is quoted explicitly. Input carries
its originating target so a queued release cannot affect a newly selected phone.
Slow device discovery runs separately from input handling.

The producer validates little-endian `HELLO`/`REQUEST` packets and rejects
unexpected descriptors or truncated messages. A connection gets one bounded
RGBA memfd, sealed against shrinking/growing; its descriptor is sent with
`SCM_RIGHTS`. The shared mapping is updated only after the next `REQUEST`
returns ownership, and its memory/descriptors are released on disconnect. Idle
connections remain valid while the shell is not drawing. Capture remains
independent and bounded to its newest frame. Pointer and keyboard handlers use
upstream's separate callbacks; wheel input uses the most recent pointer position.

Noctalia plugins and same-user processes are trusted code; this is not a sandbox
or isolation from other programs running as your user.

## Verification

```sh
python3 -B -m unittest discover -s tests -v
noctalia plugins lint /absolute/path/to/noctalia-androidconnect-v5/androidconnect
git diff --check
```

Tests cover device/endpoint/geometry parsing, conservative target selection,
late-command rejection, remote text quoting, secret handling, single-companion
locking, private runtime cleanup, windowless launch with audio on/off, readiness
from actual frames rather than logs, standalone window handoff/lifecycle and failed
launches, ADB battery parsing, validated wheel-swipe
coordinates, fitted bezel geometry, and actual FFmpeg
frames/reconnects, including transparent composed corners and advancing video.
Wireless tests cover changed ports, IP/mDNS aliases, Avahi fallback, retry limits,
per-phone association, stale results, and closing while discovery is running.
Socket
tests need a normal host session rather than a sandbox that forbids Unix bind.
The four Luau entries were also compiled with Noctalia's vendored Luau compiler.

Protocol tests additionally cover buffer ownership during capture, dropping
intermediate frames, paused drawing, reconnecting to a static frame, malformed
packets, descriptor rejection, stopping during a pending request, and preserving
occupied/replaced socket paths. `tests/panel_harness.luau` and
`tests/test_panel.luau` check the actual panel's new callbacks, all three preview
sizes, bezel mapping, text/key ordering, scroll, cancellation, and device drawers.

Optional native integration checks use the Luau and pixel-stream readers from a
Noctalia source build. From the repository root, replacing the source/build paths:

```sh
g++ -std=c++23 -O1 -pthread -I/path/to/noctalia/src \
  tests/pixel_stream_consumer.cpp /path/to/noctalia/src/ipc/pixel_stream_client.cpp \
  /path/to/noctalia/src/core/log.cpp -o /tmp/androidconnect-pixel-consumer
ANDROIDCONNECT_PIXEL_CONSUMER=/tmp/androidconnect-pixel-consumer \
  python3 -B -m unittest discover -s tests
g++ -std=c++23 -O1 -I/path/to/noctalia/third_party/luau/Common/include \
  -I/path/to/noctalia/third_party/luau/Ast/include \
  -I/path/to/noctalia/third_party/luau/Compiler/include \
  -I/path/to/noctalia/third_party/luau/VM/include \
  tests/run_luau.cpp /path/to/noctalia/build/libluau.a -o /tmp/androidconnect-run-luau
/tmp/androidconnect-run-luau --check androidconnect/*.luau
/tmp/androidconnect-run-luau tests/panel_harness.luau androidconnect/panel.luau tests/test_panel.luau
```

On 2026-10-23 the migration passed all 62 Python tests with the upstream C++
consumer enabled, plus upstream's pixel-stream client, UI reconciler, plugin
binding, and manifest tests. The actual panel's callback suite passed in upstream's
Luau runtime. A locally built, unmodified upstream shell displayed the Xiaomi 15
Ultra's live video with its fitted transparent bezel; Small idle artwork and
Large live-video layouts were inspected. Screenshots remain local. Full phone
input/media, fractional-scaling, and latency coverage still need manual testing.

## Earlier validation before the pixel-stream migration

The panel was enabled and visually inspected in the user's live patched Umbriel
session; live discovery found the ADB and KDE Connect devices, with no script/UI
warnings. An open/close check confirmed that scrcpy created no compositor window
and its owned process stopped on panel close. The real selected phone's live
video was subsequently verified in the restyled panel after fixing startup's
dependency on buffered scrcpy logs. Before reference:
[v4 screenshot](https://github.com/demencia89/noctalia-shell-androidconnect-plugin/blob/main/Docs/Screenshots/androidconnect-panel.png).
A local after screenshot was kept outside the repository because the surrounding
desktop contains private content.

The user confirmed direct PC keyboard typing and wheel scrolling in the live phone preview.
The v4-matched typography and spacing were checked in the real patched shell at
Small, Medium, and Large, including the reconnected Xiaomi 15 Ultra's live video.
The original size was restored after testing. Comparison screenshots stayed
local and were not added to the repository.
The subsequent mockup-inspired redesign was checked in the live shell, including
Small with four status rows and both right-hand action cards fully visible.
Isolated Luau checks cover all three sizes, flexible equal-width action cards,
primary-only hover/active tints, the relocated More actions, disabled/offline
controls, scrollable errors, and width-limited landscape preview geometry.
An isolated Luau runtime check also exercised keyboard ordering and scroll
direction, burst bounds, busy deferral, reversal, drag exclusion, and close/target
change cancellation against the actual panel script. Further checks covered the
size-cycle order, requested panel dimensions, right-click Back, wheel-click Home,
and cancelling an active drag on resize. Backend tests verify saved size settings
without a mirror restart; native binding tests reject invalid resize dimensions.

The earlier transport benchmark used the removed NRF1 socket protocol; its
throughput numbers do not establish the new implementation's presentation rate
or end-to-end phone latency. Those still depend on the compositor, capture path,
and phone.

Still pending: broader phone input/media and QR pairing, audio, fractional
scaling, and performance testing. The test-pattern frame path was verified in the
preceding frame-API work; that is not a claim that every phone action is verified.

The source remains based on the original Noctalia KDE Connect plugin by WerWolv
and the AndroidConnect extension by demencia89. The port retains GPLv2 licensing.
