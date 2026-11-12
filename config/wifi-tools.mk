# Userspace for the nl80211 driver: static iw and wpa_supplicant (+ libnl).
# Hashes: libnl's was computed from the GitHub release asset; the other two are
# EMPTY on purpose.  First run prints the hash of what was downloaded; verify it
# against the upstream signature (.asc / release announcement), then paste it here.
LIBNL_VERSION := 3.9.0
LIBNL_URL     := https://github.com/thom311/libnl/releases/download/libnl$(subst .,_,$(LIBNL_VERSION))/libnl-$(LIBNL_VERSION).tar.gz
LIBNL_SHA256  := aed507004d728a5cf11eab48ca4bf9e6e1874444e33939b9d3dfed25018ee9bb

IW_VERSION    := 6.9
IW_URL        := https://mirrors.edge.kernel.org/pub/software/network/iw/iw-$(IW_VERSION).tar.xz
IW_SHA256     := 3f2db22ad41c675242b98ae3942dbf3112548c60a42ff739210f2de4e98e4894

WPAS_VERSION  := 2.11
WPAS_URL      := https://w1.fi/releases/wpa_supplicant-$(WPAS_VERSION).tar.gz
WPAS_SHA256   := 912ea06f74e30a8e36fbb68064d6cdff218d8d591db0fc5d75dee6c81ac7fc0a
