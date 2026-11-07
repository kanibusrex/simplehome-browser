#!/bin/sh
# Builds ext/simplehome-pid.so, the web process extension (see pidreport.c). The browser runs
# without it; background tabs then sleep by time alone, never sooner for using the CPU.
set -e
cd "$(dirname "$(readlink -f "$0")")"
${CC:-cc} -shared -fPIC -O2 -Wall -o simplehome-pid.so pidreport.c \
    $(pkg-config --cflags --libs webkit2gtk-web-extension-4.1)
echo "Built $(pwd)/simplehome-pid.so"
