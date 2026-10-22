pkgname=ani-gtk
pkgver=0.2.0.r0.7ed54c0
pkgrel=1
pkgdesc='GTK 4 AniList library explorer with local video progress tracking'
arch=('any')
url='https://github.com/WairenD/ani-gtk'
license=('unknown')
depends=(
  'gtk4'
  'python'
  'python-gobject'
  'python-pyperclip'
  'python-requests'
)
source=("${pkgname}::git+${url}.git#branch=main")
sha256sums=('SKIP')
backup=('etc/ani-gtk/config.ini')

pkgver() {
  cd "${srcdir}/${pkgname}"
  git describe --long --tags --abbrev=7 | sed 's/^v//; s/-/.r/; s/-g/./'
}

package() {
  cd "${srcdir}/${pkgname}"

  install -d "${pkgdir}/usr/lib/${pkgname}/src"
  install -m 644 src/*.py "${pkgdir}/usr/lib/${pkgname}/src/"
  install -Dm 644 config.ini "${pkgdir}/etc/${pkgname}/config.ini"

  install -Dm 755 /dev/stdin "${pkgdir}/usr/bin/${pkgname}" <<'EOF'
#!/bin/sh
exec /usr/bin/python /usr/lib/ani-gtk/src/main.py "$@"
EOF

  install -Dm 644 /dev/stdin \
    "${pkgdir}/usr/share/applications/${pkgname}.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=AniList Library Explorer
Exec=ani-gtk
Icon=applications-internet
Terminal=false
Categories=Utility;
EOF
}
