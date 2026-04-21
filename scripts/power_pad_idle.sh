#!/usr/bin/env bash
#
# power_pad_idle.sh — toggle power-saving measures op de Zero 2 W om de
# PAD_IDLE-autonomie (batterij-uptime) te meten.
#
# ACHTERGROND
# -----------
# De organisatie-eis is 4 h alive. Op een 1300 mAh-batterij betekent dat
# ≤ 325 mA gemiddeld. Huidige CONFIG/PAD_IDLE-meting = ~500 mA. Dit
# script maakt het mogelijk om stap-voor-stap te meten hoeveel elke
# maatregel bespaart, zonder code-wijzigingen in de mission-service.
#
# WAT DIT SCRIPT WEL EN NIET DOET
# -------------------------------
# - Tijdelijk (tot reboot): alle toggles werken via runtime-APIs
#   (``rfkill``, ``vcgencmd display_power``, ``/sys/class/leds/*/trigger``).
#   Dit is bewust — zo kan je meten zonder risico dat een verkeerde
#   config.txt je Zero onbenaderbaar maakt.
# - Geen Python-code wijzigt. De mission-service (cansat-radio-protocol)
#   blijft gewoon draaien.
# - Geen CPU-governor. De user wil de full-speed CPU bewaren voor
#   AprilTag-detectie in DEPLOYED.
#
# PERMANENT MAKEN
# ---------------
# Zodra je met meten klaar bent en weet welke maatregelen je definitief
# wil, zet je de overeenkomstige lijnen in ``/boot/firmware/config.txt``:
#
#    dtoverlay=disable-bt        # Bluetooth permanent uit
#    dtoverlay=disable-wifi      # Wi-Fi permanent uit (LET OP: geen SSH
#                                # meer vanuit veld; reboot via batterij
#                                # niet herstelbaar, alleen via SD-edit)
#    dtparam=act_led_trigger=none
#    dtparam=act_led_activelow=on
#    dtparam=pwr_led_trigger=none
#    dtparam=pwr_led_activelow=on
#
# (HDMI heeft geen simpele persistent-disable op Bookworm; je kan
# ``vcgencmd display_power 0`` in /etc/rc.local of een systemd-service
# bij boot zetten.)
#
# USAGE
# -----
#   sudo ./scripts/power_pad_idle.sh status
#   sudo ./scripts/power_pad_idle.sh bluetooth off
#   sudo ./scripts/power_pad_idle.sh hdmi off
#   sudo ./scripts/power_pad_idle.sh leds off
#   sudo ./scripts/power_pad_idle.sh wifi off     # ← breekt SSH; reboot om
#                                                 #   te herstellen
#   sudo ./scripts/power_pad_idle.sh all-off      # bluetooth+hdmi+leds
#                                                 #   (géén Wi-Fi — dat moet
#                                                 #   je bewust apart doen)
#   sudo ./scripts/power_pad_idle.sh restore      # alles terug aan
#
# MEET-PROTOCOL (aanbevolen)
# --------------------------
# 1. Baseline:               (geen maatregelen)       → noteer mA
# 2. bluetooth off:                                   → noteer mA
# 3. hdmi off:                                        → noteer mA
# 4. leds off:                                        → noteer mA
# 5. wifi off (SSH breekt):                           → noteer mA
# 6. reboot + meet kale OS-baseline zonder service voor referentie
#
# Elke meting ≥ 30 s laten stabiliseren (CPU-burst na commando zakt pas
# na een halve minuut terug naar idle-niveau).

set -euo pipefail

# Vereis root; niet-root commando's (rfkill, vcgencmd write) falen stil.
if [[ $EUID -ne 0 ]]; then
	echo "Dit script moet als root draaien (sudo)." >&2
	exit 2
fi

_cmd="${1:-}"
_arg="${2:-off}"

_led_off() {
	local led="$1"
	local path="/sys/class/leds/${led}"
	[[ -d "$path" ]] || return 0
	# Trigger op 'none' zodat de kernel de LED niet meer stuurt, dan
	# brightness 0 om 'm uit te zetten. Beide zijn idempotent.
	echo none > "${path}/trigger" 2>/dev/null || true
	echo 0    > "${path}/brightness" 2>/dev/null || true
}

_led_restore() {
	local led="$1" default_trigger="$2"
	local path="/sys/class/leds/${led}"
	[[ -d "$path" ]] || return 0
	echo "${default_trigger}" > "${path}/trigger" 2>/dev/null || true
}

_bluetooth() {
	if [[ "$1" == "off" ]]; then
		rfkill block bluetooth && echo "   bluetooth: off"
	else
		rfkill unblock bluetooth && echo "   bluetooth: on"
	fi
}

_hdmi() {
	if [[ "$1" == "off" ]]; then
		/usr/bin/vcgencmd display_power 0 >/dev/null && echo "   hdmi: off"
	else
		/usr/bin/vcgencmd display_power 1 >/dev/null && echo "   hdmi: on"
	fi
}

_leds() {
	if [[ "$1" == "off" ]]; then
		_led_off ACT
		_led_off PWR
		echo "   leds: off (ACT + PWR)"
	else
		# Op Raspberry Pi OS is mmc0 de default ACT-trigger (knippert bij
		# SD-activiteit) en default-on voor PWR.
		_led_restore ACT mmc0
		_led_restore PWR default-on
		echo "   leds: on (ACT=mmc0, PWR=default-on)"
	fi
}

_wifi() {
	if [[ "$1" == "off" ]]; then
		# Geef de user 3s om commando-output te zien voor SSH breekt.
		echo "   wifi: schedule off over 3s (SSH gaat breken)..."
		(sleep 3 && rfkill block wifi) & disown
	else
		rfkill unblock wifi && echo "   wifi: on"
	fi
}

_status() {
	echo "=== power_pad_idle.sh status ==="
	echo ""
	echo "-- rfkill (radio-blocks) --"
	rfkill || true
	echo ""
	echo "-- HDMI --"
	/usr/bin/vcgencmd display_power || true
	echo ""
	echo "-- LEDs --"
	for led in ACT PWR; do
		local path="/sys/class/leds/${led}"
		if [[ -d "$path" ]]; then
			local trig
			trig=$(cat "${path}/trigger" 2>/dev/null | grep -oE '\[[^]]+\]' || echo '?')
			local brt
			brt=$(cat "${path}/brightness" 2>/dev/null || echo '?')
			echo "   ${led}: trigger=${trig} brightness=${brt}"
		fi
	done
	echo ""
	echo "-- cansat-radio-protocol service --"
	systemctl is-active cansat-radio-protocol.service 2>/dev/null || true
}

case "${_cmd}" in
	status)
		_status
		;;
	bluetooth)
		_bluetooth "${_arg}"
		;;
	hdmi)
		_hdmi "${_arg}"
		;;
	leds)
		_leds "${_arg}"
		;;
	wifi)
		_wifi "${_arg}"
		;;
	all-off)
		# Alles behalve Wi-Fi — die is bewust opt-in omdat hij SSH breekt.
		_bluetooth off
		_hdmi off
		_leds off
		echo ""
		echo "all-off klaar. Wi-Fi staat nog aan (SSH werkt)."
		echo "Voor totale shutdown: sudo $0 wifi off"
		;;
	restore)
		_bluetooth on
		_hdmi on
		_leds on
		_wifi on
		echo ""
		echo "Alles teruggezet. (Tip: reboot is gelijkwaardig.)"
		;;
	""|-h|--help|help)
		cat <<'HELP'
power_pad_idle.sh — power-saving test toggles voor de Zero 2 W.

Gebruik (steeds als root):
  sudo ./scripts/power_pad_idle.sh status              toon huidige state
  sudo ./scripts/power_pad_idle.sh bluetooth off|on
  sudo ./scripts/power_pad_idle.sh hdmi      off|on
  sudo ./scripts/power_pad_idle.sh leds      off|on
  sudo ./scripts/power_pad_idle.sh wifi      off|on    # off breekt SSH!
  sudo ./scripts/power_pad_idle.sh all-off             # BT + HDMI + LEDs uit
  sudo ./scripts/power_pad_idle.sh restore             # alles aan

Alle wijzigingen zijn TIJDELIJK — een reboot herstelt de default state.
Meet-protocol + permanent maken: zie de kop van dit script.
HELP
		;;
	*)
		echo "Onbekend commando: ${_cmd}" >&2
		echo "Zie: sudo $0 --help" >&2
		exit 1
		;;
esac
