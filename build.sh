#!/usr/bin/env bash
# Download nginx, apply the matching core patch and build it with this module.
#   ./build.sh 1.30.7 [extra ./configure arguments...]
# The binary ends up in build/nginx-<version>/objs/nginx.
set -Eeuo pipefail

ver=${1:?usage: ./build.sh NGINX_VERSION [configure args...]}
shift

case $ver in
    1.31.*)                 patch=nginx-1.31.patch ;;
    1.2[89].*|1.30.*)       patch=nginx-1.28-1.30.patch ;;
    1.2[4-6].*)             patch=nginx-1.24-1.26.patch ;;
    1.1[2-9].*|1.2[0-3].*)  patch=legacy/nginx-1.12-1.22.patch ;;
    *) echo "no core patch for nginx $ver" >&2; exit 1 ;;
esac

here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$here/build"
cd "$here/build"

[ -f "nginx-$ver.tar.gz" ] || curl -fsSLO "https://nginx.org/download/nginx-$ver.tar.gz"
rm -rf "./nginx-$ver"
tar xzf "nginx-$ver.tar.gz"
cd "nginx-$ver"

patch -p1 < "$here/patch/$patch"
./configure --with-stream --add-module="$here" "$@"
make -j"$(getconf _NPROCESSORS_ONLN)"

echo "built $PWD/objs/nginx with patch/$patch"
