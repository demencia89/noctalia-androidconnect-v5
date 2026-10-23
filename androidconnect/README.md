# AndroidConnect for Noctalia v5

Android device status, KDE Connect actions, and interactive scrcpy control in a
native Noctalia panel. KDE Connect is optional for ADB control and mirroring.

Version **2.3.0** requires Noctalia **plugin API 42 or newer**, with upstream
`ui.pixelStream` and content-sized panel height. No custom shell patch is needed;
older v5 builds and the former `ui.frame` patch are not compatible.
See upstream's [Pixel streams documentation](https://github.com/noctalia-dev/noctalia/blob/main/docs/user/plugins/development/declarative-ui.mdx#pixel-streams).

The three preview-size presets remain saved. Panel height follows the content;
panel width is fixed at 680px because upstream does not provide `panel.setSize`.

See the [repository setup guide](https://github.com/demencia89/noctalia-androidconnect-v5#readme)
for installation, dependencies, controls, testing, and limitations.

The [v4 QML plugin](https://github.com/demencia89/noctalia-shell-androidconnect-plugin)
is maintained separately. This port retains the original GPLv2 license and
credits the original [Noctalia KDE Connect plugin](https://github.com/WerWolv/noctalia-kde-connect)
by WerWolv and the AndroidConnect extension by demencia89.
