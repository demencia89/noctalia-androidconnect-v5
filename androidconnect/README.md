# AndroidConnect for Noctalia v5

Android device status, KDE Connect actions, and interactive scrcpy control in a
native Noctalia panel. KDE Connect is optional for ADB control and mirroring.

This is a development preview. It requires a Noctalia build containing the
proposed `ui.frame` and `panel.setSize` APIs, currently API 40:
[shell PR #4961](https://github.com/noctalia-dev/noctalia/pull/4961).
It does not work on an unpatched Noctalia v5 release.

See the [repository setup guide](https://github.com/demencia89/noctalia-androidconnect-v5#readme)
for installation, dependencies, controls, testing, and limitations.

The [v4 QML plugin](https://github.com/demencia89/noctalia-shell-androidconnect-plugin)
is maintained separately. This port retains the original GPLv2 license and
credits the original [Noctalia KDE Connect plugin](https://github.com/WerWolv/noctalia-kde-connect)
by WerWolv and the AndroidConnect extension by demencia89.
