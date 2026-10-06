Download the file for your computer, open it, and NUauto's setup screen walks you through the rest (Claude Code,
your Google sign-in, your sheet, NUworks, your resume). Nothing else to install by hand.

**Linux** (most distributions from 2022 on): `NUauto-…-linux-x86_64.AppImage` (or `-aarch64` for ARM). Make it
executable (right-click > Properties > "Allow executing as program", or `chmod +x NUauto-*.AppImage`), then
double-click it. Its own window needs GTK's WebKit (python3-gobject + webkitgtk, on most GNOME desktops); without it
NUauto opens in a Chrome-style app window or your browser.

**macOS** (12 or newer): `NUauto-…-macos-arm64.dmg` for Apple-silicon Macs, `-x86_64` for Intel. Open the DMG and
drag NUauto to Applications. The app is not signed by Apple, so the first time: open it, then go to System Settings >
Privacy & Security and press **Open Anyway**. (If macOS says the app is damaged, run
`xattr -dr com.apple.quarantine /Applications/NUauto.app` in Terminal once.)

Your files live in `~/.local/share/NUauto` (Linux) or `~/Library/Application Support/NUauto` (macOS). To update,
download the new version; your files stay.
