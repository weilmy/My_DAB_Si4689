#!/usr/bin/env python3
"""
test_dls_irq.py  –  Nachweis: Si4689 zieht GPIO 23 (INTB) bei DLS-Daten physisch LOW.

Voraussetzungen:
  - main.py muss gestoppt sein (exklusiver SPI-Zugriff)
  - Ausführen als: sudo ~/my_venv_314/bin/python3 test_dls_irq.py

Ablauf:
  1.  gpiochip-Erkennung: welches /dev/gpiochipX trägt GPIO23?
  2.  Si4689 vollständig initialisieren (DAB, Kanal 12C, Swiss Pop+ starten)
  3.  GPIO23 aus RPi.GPIO freigeben, dann gpiod übernimmt die Leitung
  4.  Ruhepegel lesen (erwartet: HIGH)
  5.  INT_CTL_ENABLE (0x0000) Bit4=DSRVIEN setzen
      DIGITAL_SERVICE_INT_SOURCE (0x8100) Bit0=DSRVPCKTINT setzen
  6.  Warten auf Falling Edge oder sofortiger LOW-Pegel (Paket schon im Buffer)
  7.  Bestätigung via get_digital_service_data(status_only=True) → packet_ready

Referenz: AN649 §7.7.2 INT_CTL_ENABLE, §7.7.3 DIGITAL_SERVICE_INT_SOURCE
"""

import sys
import time
import datetime

# gpiod ist als System-Paket installiert (python3-libgpiod),
# nicht im venv – Pfad explizit ergänzen.
sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, "/home/weilmy/My_DAB_Si4689")

import gpiod
from gpiod.line import Direction, Edge, Bias, Value

from hardware.si4689_init import Si4689Manager

# ---------------------------------------------------------------------------
# Testkonfiguration
# ---------------------------------------------------------------------------
CHANNEL       = "12C"          # SRG SSR Ensemble
SERVICE_ID    = 0x000042F1     # Swiss Pop+
COMPONENT_ID  = 0x00000010     # CID gemäss Vorgabe

INT_LINE_LABEL    = "GPIO23"   # Erwartetes Label in gpioinfo
INT_LINE_OFFSET   = 23         # BCM-Offset / physische Leitungsnummer
EDGE_TIMEOUT_S    = 45         # DLS-Intervall Swiss Pop+: typ. 10–30 s

PROP_INT_CTL_ENABLE             = 0x0000
PROP_DIGITAL_SERVICE_INT_SOURCE = 0x8100
DSRVIEN     = 0x0010   # INT_CTL_ENABLE Bit 4
DSRVPCKTINT = 0x0001   # DIGITAL_SERVICE_INT_SOURCE Bit 0


# ---------------------------------------------------------------------------
def find_gpiochip(line_label: str, line_offset: int) -> str | None:
    """
    Liefert den Pfad des GPIO-Chips, dessen Leitung *line_offset* das
    Label *line_label* trägt, z.B. "/dev/gpiochip0".
    Gibt None zurück, wenn kein passender Chip gefunden wird.
    """
    from pathlib import Path
    for dev in sorted(Path("/dev").glob("gpiochip*")):
        if not gpiod.is_gpiochip_device(str(dev)):
            continue
        try:
            with gpiod.Chip(str(dev)) as chip:
                info = chip.get_info()
                if line_offset >= info.num_lines:
                    continue
                linfo = chip.get_line_info(line_offset)
                if linfo.name == line_label:
                    return str(dev)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
def wait_for_dab_lock(manager: Si4689Manager, timeout: float = 20.0) -> bool:
    """Wartet bis VALID + ACQ + FIC ≥ 90%."""
    radio = manager._radio
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            st = radio.dab_digrad_status(stc_ack=False, attune=False)
            if st.get("valid") and st.get("acq") and st.get("fic_quality", 0) >= 90:
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 62)
    print("  test_dls_irq.py  –  Si4689 INT-Pin (GPIO 23) Nachweis")
    print("=" * 62)

    # ------------------------------------------------------------------
    # Schritt 1: GPIO-Chip ermitteln
    # ------------------------------------------------------------------
    print(f"\n[1] GPIO-Chip-Suche für Label '{INT_LINE_LABEL}' (Offset {INT_LINE_OFFSET}) …")
    chip_path = find_gpiochip(INT_LINE_LABEL, INT_LINE_OFFSET)
    if chip_path is None:
        print(f"    FEHLER: Kein GPIO-Chip mit Leitung '{INT_LINE_LABEL}' gefunden.")
        sys.exit(1)
    print(f"    → Chip: {chip_path}  (Leitung {INT_LINE_OFFSET} = '{INT_LINE_LABEL}') ✓")

    # ------------------------------------------------------------------
    # Schritt 2: Si4689 initialisieren (DAB)
    # ------------------------------------------------------------------
    print("\n[2] Si4689 initialisieren (DAB-Firmware, Kanal 12C) …")
    manager = Si4689Manager(verbose=False)
    if not manager.initialize():
        print("    FEHLER: Si4689-Initialisierung fehlgeschlagen.")
        sys.exit(1)

    radio = manager._radio   # Si4689-Instanz für set_property / get_digital_service_data

    # DAB-Tune 12C
    print(f"    Tune → Kanal {CHANNEL} …")
    if not manager.dab_tune(CHANNEL):
        print("    FEHLER: dab_tune fehlgeschlagen.")
        manager.close()
        sys.exit(1)

    # Auf DAB-Lock warten
    print("    Warten auf DAB-Lock (VALID+ACQ+FIC≥90%) …", end="", flush=True)
    if not wait_for_dab_lock(manager, timeout=20.0):
        print(" FEHLER: kein Lock innerhalb 20 s.")
        manager.close()
        sys.exit(1)
    print(" OK")

    # STCINT quittieren
    try:
        radio.dab_digrad_status(stc_ack=True, attune=False)
    except Exception:
        pass

    # Swiss Pop+ starten
    print(f"    START_DIGITAL_SERVICE SID=0x{SERVICE_ID:08X} CID=0x{COMPONENT_ID:08X} …")
    try:
        radio.dab_start_service(SERVICE_ID, COMPONENT_ID)
    except Exception as exc:
        print(f"    FEHLER: dab_start_service: {exc}")
        manager.close()
        sys.exit(1)

    # kurze Pause – Audiodienst aufbauen lassen
    print("    Dienst läuft – 2 s warten …")
    time.sleep(2.0)

    # ------------------------------------------------------------------
    # Schritt 3: GPIO23 aus RPi.GPIO freigeben, gpiod übernimmt
    # ------------------------------------------------------------------
    print(f"\n[3] GPIO{INT_LINE_OFFSET} aus RPi.GPIO freigeben …")
    try:
        import RPi.GPIO as GPIO
        GPIO.cleanup(INT_LINE_OFFSET)
        print(f"    GPIO{INT_LINE_OFFSET} via lgpio.gpio_free() freigegeben ✓")
    except Exception as exc:
        print(f"    Warnung: GPIO.cleanup({INT_LINE_OFFSET}) Fehler: {exc} (fortgesetzt)")

    # gpiod: Leitung mit Pull-Up und Falling-Edge-Detection anfordern
    print(f"    gpiod: Leitung {INT_LINE_OFFSET} auf {chip_path} anfordern …")
    try:
        req = gpiod.request_lines(
            chip_path,
            consumer="test_dls_irq",
            config={
                INT_LINE_OFFSET: gpiod.LineSettings(
                    direction=Direction.INPUT,
                    bias=Bias.PULL_UP,
                    edge_detection=Edge.FALLING,
                )
            },
        )
    except Exception as exc:
        print(f"    FEHLER: gpiod.request_lines: {exc}")
        manager.close()
        sys.exit(1)
    print(f"    gpiod: Leitung {INT_LINE_OFFSET} angefordert ✓")

    # ------------------------------------------------------------------
    # Schritt 4: Ruhepegel lesen
    # ------------------------------------------------------------------
    print(f"\n[4] Ruhepegel GPIO{INT_LINE_OFFSET} …")
    idle_val = req.get_value(INT_LINE_OFFSET)
    idle_str = "HIGH" if idle_val == Value.ACTIVE else "LOW"
    expected = "✓" if idle_val == Value.ACTIVE else "⚠  WARNUNG: PIN schon LOW – Paket im Buffer?"
    print(f"    Pegel: {idle_str}  {expected}")

    # ------------------------------------------------------------------
    # Schritt 5: INT_CTL_ENABLE + DIGITAL_SERVICE_INT_SOURCE setzen
    # ------------------------------------------------------------------
    print(f"\n[5] Interrupt-Properties setzen …")
    print(f"    INT_CTL_ENABLE (0x{PROP_INT_CTL_ENABLE:04X}) = 0x{DSRVIEN:04X}  (DSRVIEN Bit4)")
    radio.set_property(PROP_INT_CTL_ENABLE, DSRVIEN)

    print(f"    DIGITAL_SERVICE_INT_SOURCE (0x{PROP_DIGITAL_SERVICE_INT_SOURCE:04X}) = 0x{DSRVPCKTINT:04X}  (DSRVPCKTINT Bit0)")
    radio.set_property(PROP_DIGITAL_SERVICE_INT_SOURCE, DSRVPCKTINT)
    print("    Properties gesetzt ✓")

    # Sofort-Check: Pin schon LOW? (Paket lag bereits im Buffer)
    immediate_val = req.get_value(INT_LINE_OFFSET)
    if immediate_val != Value.ACTIVE:
        print(f"\n    → GPIO{INT_LINE_OFFSET} ist bereits LOW direkt nach Property-Set.")
        print("      (Paket war bereits im Buffer – das ist ein Erfolg!)")
        _confirm_packet(radio)
        _cleanup(req, radio, manager)
        return

    # ------------------------------------------------------------------
    # Schritt 6: Warten auf Falling Edge
    # ------------------------------------------------------------------
    print(f"\n[6] Warten auf Falling Edge an GPIO{INT_LINE_OFFSET} "
          f"(Timeout {EDGE_TIMEOUT_S} s) …")
    print("    (Swiss Pop+ DLS-Intervall: typ. 10–30 s)")

    t_start = time.monotonic()
    got_edge = req.wait_edge_events(
        timeout=datetime.timedelta(seconds=EDGE_TIMEOUT_S)
    )

    elapsed = time.monotonic() - t_start

    if not got_edge:
        print(f"\n    TIMEOUT nach {elapsed:.1f} s – kein INT-Puls empfangen.")
        print("    Mögliche Ursachen:")
        print("      • Swiss Pop+ sendet gerade keine DLS-Daten")
        print("      • INTBOUTEN (PIN_CONFIG_ENABLE Bit15) nicht gesetzt")
        print("      • Verdrahtungsproblem GPIO23")
        _cleanup(req, radio, manager)
        sys.exit(2)

    events = req.read_edge_events(1)
    evt = events[0]
    print(f"\n    ✓ Falling Edge erkannt nach {elapsed:.2f} s")
    print(f"      Timestamp: {evt.timestamp_ns} ns  "
          f"(Monotonic {evt.timestamp_ns / 1e9:.3f} s)")

    # ------------------------------------------------------------------
    # Schritt 7: Bestätigung via get_digital_service_data
    # ------------------------------------------------------------------
    _confirm_packet(radio)
    _cleanup(req, radio, manager)


# ---------------------------------------------------------------------------
def _confirm_packet(radio) -> None:
    print(f"\n[7] Bestätigung: GET_DIGITAL_SERVICE_DATA(status_only=True) …")
    try:
        status = radio.get_digital_service_data(status_only=True, ack=False)
        pkt_ready = status.get("packet_ready", False)
        buf_count = status.get("buffer_count", 0)
        print(f"    packet_ready = {pkt_ready}   buffer_count = {buf_count}")
        if pkt_ready:
            print("    ✓ NACHWEIS ERBRACHT: packet_ready=True nach Falling Edge")
            print("      Der Si4689 zieht GPIO 23 physisch LOW bei neuen DLS-Daten.")
            print("      Fix für main.py: INT_CTL_ENABLE (0x0000) Bit4=DSRVIEN")
            print("      zusätzlich zu DIGITAL_SERVICE_INT_SOURCE (0x8100) setzen.")
        else:
            print("    ⚠  packet_ready=False nach Edge – transient (Paket inzwischen"
                  " von anderem Prozess gelesen oder Edge war Noise).")
    except Exception as exc:
        print(f"    FEHLER: get_digital_service_data: {exc}")


def _cleanup(req, radio, manager) -> None:
    print("\n[8] Aufräumen …")
    try:
        # DSRVIEN deaktivieren, damit INT-Pin nicht offen bleibt
        radio.set_property(PROP_INT_CTL_ENABLE, 0x0000)
        radio.set_property(PROP_DIGITAL_SERVICE_INT_SOURCE, 0x0000)
    except Exception:
        pass
    try:
        req.release()
    except Exception:
        pass
    try:
        manager.close()
    except Exception:
        pass
    print("    Fertig.")


if __name__ == "__main__":
    main()
