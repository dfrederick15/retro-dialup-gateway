# Build the Windows-side tools on Linux with mingw-w64 (i686).
#   Debian/Ubuntu: apt install gcc-mingw-w64-i686     Fedora: dnf install mingw32-gcc
#
# -march=i486      : no CMOV etc., so it runs on 486/Pentium-class 9x machines
# subsystem 4.0    : loadable by Windows 95 through XP
# -nostdlib        : the mingw CRT startup calls APIs missing on 9x
CC      = i686-w64-mingw32-gcc
CFLAGS  = -Os -s -march=i486 -mtune=pentium -ffreestanding -fno-builtin \
          -fno-stack-protector -fno-asynchronous-unwind-tables -Wall -Wno-unused-function
LDFLAGS = -nostdlib -nostartfiles -e _entry \
          -Wl,--major-subsystem-version,4,--minor-subsystem-version,0 \
          -Wl,--major-os-version,4,--minor-os-version,0
OUT     = ../bin

all: $(OUT)/SERSHELL.EXE $(OUT)/SERSHTCP.EXE $(OUT)/REBOOT.EXE $(OUT)/KILL.EXE

$(OUT)/SERSHELL.EXE: sershell.c common.h
	$(CC) $(CFLAGS) $(LDFLAGS) -Wl,--subsystem,console -o $@ $< -lkernel32
$(OUT)/SERSHTCP.EXE: sershtcp.c common.h
	$(CC) $(CFLAGS) $(LDFLAGS) -Wl,--subsystem,console -o $@ $< -lkernel32 -lwsock32 -ladvapi32
$(OUT)/REBOOT.EXE: reboot.c common.h
	$(CC) $(CFLAGS) $(LDFLAGS) -Wl,--subsystem,windows -o $@ $< -lkernel32 -luser32

$(OUT)/KILL.EXE: kill.c common.h
	$(CC) $(CFLAGS) $(LDFLAGS) -Wl,--subsystem,console -o $@ $< -lkernel32

# verify: PE subsystem 4.0, only 9x-safe DLLs, no i686-only instructions
check: all
	@for f in $(OUT)/*.EXE; do \
	  echo "== $$f"; \
	  i686-w64-mingw32-objdump -p $$f | grep -E 'SubsystemVersion|DLL Name'; \
	  n=$$(i686-w64-mingw32-objdump -d $$f | grep -cE '\scmov|\sfcmov|\sfcomi'); \
	  echo "   i686-only instructions: $$n"; test $$n -eq 0; \
	done

clean:
	rm -f $(OUT)/*.EXE
.PHONY: all check clean
