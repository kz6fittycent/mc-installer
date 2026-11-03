# Sourced by the launch script. Not executable on its own.
#
# The .desktop file has PrefersNonDefaultGPU=true, so on a laptop with two
# GPUs GNOME starts Minecraft with settings that move it to the faster one:
# NVIDIA's PRIME offload variables, or DRI_PRIME for Intel/AMD (Mesa).
#
# GNOME also does this when it shouldn't, for example on a desktop whose
# only GPU is NVIDIA (it counts that one as "discrete"). Offloading to the
# GPU that already drives the display is pointless, and with NVIDIA it
# breaks things: the launcher's Skins tab says WebGL isn't supported (its
# Chromium GPU process crashes) and the game window can come out see-through.
#
# So drop the offload settings when they point at the GPU that drives the
# display (the kernel marks it with boot_vga=1), or when there's only one GPU.
# If it can't be told, GNOME's choice stands.
#
# GPU_ENV_DRM can point at another sysfs drm folder, for testing.

# The PCI device folder of each GPU, once each.
_gpu_devices() {
	local card
	for card in "${GPU_ENV_DRM:-/sys/class/drm}"/card*; do
		[[ "${card##*/}" =~ ^card[0-9]+$ ]] || continue  # skip connectors (card1-DP-1)
		[ -e "$card/device" ] && readlink -f "$card/device"
	done | sort -u
}

# True if one of the given GPUs drives the display.
_drives_display() {
	local dev
	for dev; do
		[ "$(cat "$dev/boot_vga" 2>/dev/null)" = 1 ] && return 0
	done
	return 1
}

# The GPU DRI_PRIME names, if it names one: pci-0000_0a_00_0 or 1002:73bf
# (vendor:device), either maybe ending in "!". DRI_PRIME=1 ("the other
# GPU") names none.
_dri_prime_device() {
	local want="${1%!}" dev
	for dev in "${gpus[@]}"; do
		case "$want" in
			pci-*)
				local addr="${want#pci-}"  # 0000_0a_00_0 -> 0000:0a:00.0
				addr="${addr:0:4}:${addr:5:2}:${addr:8:2}.${addr:11}"
				[ "${dev##*/}" = "$addr" ] && echo "$dev" && return
				;;
			????:????)
				[ "$(cat "$dev/vendor" 2>/dev/null)" = "0x${want%:*}" ] &&
					[ "$(cat "$dev/device" 2>/dev/null)" = "0x${want#*:}" ] && echo "$dev" && return
				;;
		esac
	done
}

fix_gpu_offload() {
	local gpus=() nvidia=() dev target
	mapfile -t gpus < <(_gpu_devices)

	if [ -n "${__NV_PRIME_RENDER_OFFLOAD:-}" ]; then
		for dev in "${gpus[@]}"; do
			[ "$(cat "$dev/vendor" 2>/dev/null)" = 0x10de ] && nvidia+=("$dev")
		done
		if [ "${#gpus[@]}" -eq 1 ] || _drives_display "${nvidia[@]}"; then
			unset __NV_PRIME_RENDER_OFFLOAD __GLX_VENDOR_LIBRARY_NAME __VK_LAYER_NV_optimus \
				VK_LOADER_DRIVERS_SELECT
		fi
	fi

	if [ -n "${DRI_PRIME:-}" ]; then
		target="$(_dri_prime_device "$DRI_PRIME")"
		if [ "${#gpus[@]}" -eq 1 ] || { [ -n "$target" ] && _drives_display "$target"; }; then
			unset DRI_PRIME
		fi
	fi
}
