#!/usr/bin/env python3
"""Stand-in Moonraker for developing the screen without a printer.

Serves the endpoints kobra_screen uses and simulates heating, a print job and
an ACE dryer.  `python3 fake_moonraker.py [port]`
"""

import configparser
import copy
import io
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakePrinter:
    def __init__(self):
        self.lock = threading.Lock()
        self.klippy = "ready"
        self.gcodes = []  # every script received, for tests
        self.fail_next_gcode = None
        self.rebooted = False
        self.files = [{"path": "kutu_%02d.gcode" % i, "modified": 1763200000 + i * 3600, "size": 1000 * i}
                      for i in range(1, 9)]
        self.history_jobs = [
            {
                "job_id": "000003",
                "exists": True,
                "filename": "kutu_08.gcode",
                "status": "completed",
                "start_time": 1763210000.0,
                "end_time": 1763215400.0,
                "print_duration": 5280.0,
                "total_duration": 5400.0,
                "filament_used": 4210.0,
                "metadata": {"estimated_time": 5400, "filament_total": 4210.0},
            },
            {
                "job_id": "000002",
                "exists": True,
                "filename": "kutu_07.gcode",
                "status": "cancelled",
                "start_time": 1763190000.0,
                "end_time": 1763191200.0,
                "print_duration": 1140.0,
                "total_duration": 1200.0,
                "filament_used": 950.0,
                "metadata": {"estimated_time": 3600, "filament_total": 3100.0},
            },
            {
                "job_id": "000001",
                "exists": False,
                "filename": "silinmis_parca.gcode",
                "status": "error",
                "start_time": 1763150000.0,
                "end_time": 1763150600.0,
                "print_duration": 540.0,
                "total_duration": 600.0,
                "filament_used": 320.0,
                "metadata": {"estimated_time": 2400, "filament_total": 1800.0},
            },
        ]
        self.history_totals = {
            "total_jobs": 3,
            "total_time": 7200.0,
            "total_print_time": 6960.0,
            "total_filament_used": 5480.0,
            "longest_job": 5400.0,
            "longest_print": 5280.0,
        }
        self._active_job = None
        self.temperature_store_enabled = True
        self.temperature_store = {
            "extruder": {
                "temperatures": [24.0 + (i % 3) * 0.2 for i in range(60)],
                "targets": [0.0] * 60,
                "powers": [0.0] * 60,
            },
            "heater_bed": {
                "temperatures": [23.0 + (i % 2) * 0.1 for i in range(60)],
                "targets": [0.0] * 60,
                "powers": [0.0] * 60,
            },
        }
        self.gcode_store = [
            {"message": "Klipper state: Ready", "time": 1763210000.0, "type": "response"},
        ]
        self.update_status = None
        self.fail_next_ace = None  # message of a "!! ACE[0]: ..." answer for the next ACE command
        self.ace_info_lines = True  # answer accepted ACE commands with "// ACE[0]: ..." like the driver does
        self.status = {
            "print_stats": {"state": "standby", "filename": "", "print_duration": 0.0, "filament_used": 0.0,
                            "message": "", "info": {}},
            "virtual_sdcard": {"progress": 0.0},
            "exclude_object": {"objects": [], "excluded_objects": [], "current_object": None},
            "extruder": {"temperature": 24.0, "target": 0.0},
            "heater_bed": {"temperature": 23.0, "target": 0.0},
            "toolhead": {"homed_axes": "", "position": [0.0, 0.0, 0.0, 0.0]},
            "fan": {"speed": 0.0},
            "gcode_move": {"homing_origin": [0.0, 0.0, 0.0, 0.0], "gcode_position": [0.0, 0.0, 0.0, 0.0],
                           "speed_factor": 1.0, "extrude_factor": 1.0},
            "leviq3": {"z_offset": -0.165, "temp_comp": 0.0, "auto_zoffset_on_off": 0},
            "ace_state": {
                "ace_instances": 1,
                "current_index": 1,
                "endless_spool_enabled": False,
                "endless_spool_match_mode": "exact",
                "ace_pro_enabled": True,
                "toolhead_sensor": False,
                "rdm_sensor": False,
            },
            "ace_instance_0": {
                "instance": 0,
                "status": "ready",
                "temp": 26,
                "fan_speed": 0,
                "enable_rfid": 1,
                "rfid_sync_enabled": True,
                "feed_assist_slot": 1,
                "feed_assist_count": 0,
                "cont_assist_time": 0.0,
                "protocol": "ace1_json",
                "model": "Anycubic Color Engine Pro",
                "firmware": "V1.3.863",
                "boot_firmware": "V1.0.1",
                "usb_port": "/dev/ttyACM0",
                "connection_state": "connected",
                "dryer_status": {"status": "stop", "target_temp": 0, "duration": 0, "remain_time": 0},
                "slots": [
                    {
                        "index": 0, "tool": 0, "status": "ready", "material": "PLA",
                        "color": [245, 245, 245], "temp": 210, "rfid": 2,
                        "sku": "AHPLWH-101", "brand": "Anycubic",
                        "extruder_temp": {"min": 190, "max": 230},
                        "hotbed_temp": {"min": 50, "max": 60},
                        "diameter": 1.75, "total": 1000, "current": 820,
                    },
                    {"index": 1, "tool": 1, "status": "ready", "material": "PETG",
                     "color": [40, 110, 230], "temp": 240, "rfid": False},
                    {"index": 2, "tool": 2, "status": "ready", "material": "PLA",
                     "color": [220, 40, 40], "temp": 210, "rfid": False},
                    {"index": 3, "tool": 3, "status": "empty", "material": "",
                     "color": [0, 0, 0], "temp": 0, "rfid": False},
                ]},
            "output_pin nozzle_light": {"value": 0.0},
            "output_pin logo_light": {"value": 1.0},
        }
        self.metadata = {"referenced_tools": [0], "filament_type": "PLA", "filament_colors": ["#E03030"],
                         "estimated_time": 5400, "layer_height": 0.2, "first_layer_height": 0.2, "object_height": 24.0,
                         "filament_total": 4210.0,
                         "thumbnails": [{"width": 32, "height": 32, "relative_path": ".thumbs/kutu-32x32.png"},
                                        {"width": 96, "height": 96, "relative_path": ".thumbs/kutu-96x96.png"}]}
        self.config_files = {}
        self.base_settings = {
            "extruder": {"max_temp": 320.0, "min_temp": -200.0, "min_extrude_temp": 170.0,
                         "max_extrude_only_distance": 100.0},
            "heater_bed": {"max_temp": 120.0, "min_temp": -200.0},
            "printer": {"max_velocity": 600.0, "max_accel": 20000.0, "max_z_velocity": 20.0, "max_z_accel": 3000.0},
            "verify_heater extruder": {"max_error": 120.0, "check_gain_time": 30.0,
                                       "hysteresis": 5.0, "heating_gain": 1.0},
            "verify_heater heater_bed": {"max_error": 120.0, "check_gain_time": 300.0,
                                         "hysteresis": 5.0, "heating_gain": 1.0},
            "leviq3": {"max_auto_travel": 2.0, "noise_diff": 2500.0},
            "ace": {
                "max_dryer_temperature": "60",
                "tangle_detection": False,
                "tangle_detection_length": 15.0,
                "total_max_feeding_length": "2600",
                "toolchange_load_length": "3000",
                "feed_speed": "60",
                "retract_speed": "50",
                "timeout_multiplier": 2,
                "runout_debounce_count": 1,
                "ace_connection_supervision": True,
                "rfid_inventory_sync_enabled": True,
            },
            "temperature_sensor ace_temp": {"min_temp": 0.0, "max_temp": 70.0},
        }
        self.settings = copy.deepcopy(self.base_settings)

    def reload_config(self):
        settings = copy.deepcopy(self.base_settings)
        raw = self.config_files.get("open_kobra_limits.cfg")
        if raw:
            parser = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
            parser.read_string(raw.decode("utf-8", errors="replace"))
            for section in parser.sections():
                sec_l = section.strip().lower()
                if sec_l not in settings:
                    raise ValueError("Section '%s' is not a valid config section" % sec_l)
                for option, val in parser.items(section):
                    opt_l = option.strip().lower()
                    if opt_l not in settings[sec_l]:
                        raise ValueError("Option '%s' is not valid in section '%s'" % (opt_l, sec_l))
                    text = val.strip()
                    low = text.lower()
                    if isinstance(settings[sec_l][opt_l], bool) or low in ("true", "false"):
                        if low in ("true", "yes", "on", "1"):
                            settings[sec_l][opt_l] = True
                        elif low in ("false", "no", "off", "0"):
                            settings[sec_l][opt_l] = False
                        else:
                            raise ValueError("Invalid boolean '%s' for '%s.%s'" % (text, sec_l, opt_l))
                        continue
                    try:
                        settings[sec_l][opt_l] = float(text)
                    except ValueError:
                        settings[sec_l][opt_l] = text
        pr = settings.get("printer", {})
        if pr.get("max_z_velocity", 0) > pr.get("max_velocity", 0):
            raise ValueError("max_z_velocity must have maximum of max_velocity")
        if pr.get("max_z_accel", 0) > pr.get("max_accel", 0):
            raise ValueError("max_z_accel must have maximum of max_accel")
        ext = settings.get("extruder", {})
        if ext.get("max_temp", 0) <= ext.get("min_temp", -273.15):
            raise ValueError("extruder max_temp must be above min_temp")
        if ext.get("min_extrude_temp", 0) > ext.get("max_temp", 0):
            raise ValueError("min_extrude_temp must not exceed max_temp")
        self.settings = settings

    @staticmethod
    def thumbnail():
        """A small generated picture standing in for a slicer thumbnail."""
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (96, 96), (44, 47, 52))
        draw = ImageDraw.Draw(image)
        draw.polygon([(48, 14), (82, 32), (48, 50), (14, 32)], fill=(90, 205, 130))
        draw.polygon([(14, 32), (48, 50), (48, 84), (14, 66)], fill=(0, 150, 60))
        draw.polygon([(82, 32), (48, 50), (48, 84), (82, 66)], fill=(0, 110, 45))
        data = io.BytesIO()
        image.save(data, "PNG")
        return data.getvalue()

    def _finish_active_job(self, status_name):
        if self._active_job is None:
            return
        job = self._active_job
        self._active_job = None
        dur = float(self.status["print_stats"].get("print_duration") or 0.0)
        prog = float(self.status["virtual_sdcard"].get("progress") or 0.0)
        filament = float(self.status["print_stats"].get("filament_used") or 0.0)
        if filament <= 0.0:
            filament = round(float(self.metadata.get("filament_total", 0.0)) * prog, 1)
        job.update(
            status=status_name,
            end_time=job["start_time"] + dur,
            print_duration=dur,
            total_duration=dur,
            filament_used=filament,
        )
        self.history_jobs.insert(0, job)
        self.history_totals["total_jobs"] += 1
        self.history_totals["total_time"] += dur
        self.history_totals["total_print_time"] += dur
        self.history_totals["total_filament_used"] += filament
        self.history_totals["longest_job"] = max(self.history_totals["longest_job"], dur)
        self.history_totals["longest_print"] = max(self.history_totals["longest_print"], dur)

    def tick(self, dt=1.0):
        """Advance the simulation; called by the server thread or by tests."""
        with self.lock:
            for name in ("extruder", "heater_bed"):
                heater = self.status[name]
                goal = heater["target"] or 24.0
                heater["temperature"] += max(-3.0, min(8.0, goal - heater["temperature"])) * dt
                store = self.temperature_store.get(name)
                if store is not None:
                    store["temperatures"].append(round(heater["temperature"], 2))
                    store["targets"].append(round(heater["target"], 2))
                    store["powers"].append(1.0 if heater["target"] > heater["temperature"] else 0.0)
                    for k in ("temperatures", "targets", "powers"):
                        if len(store[k]) > 1200:
                            del store[k][:-1200]
            stats = self.status["print_stats"]
            if stats["state"] == "printing":
                stats["print_duration"] += 60 * dt
                progress = min(1.0, self.status["virtual_sdcard"]["progress"] + 0.02 * dt)
                self.status["virtual_sdcard"]["progress"] = progress
                stats["filament_used"] = round(float(self.metadata.get("filament_total", 4210.0)) * progress, 1)
                self.status["gcode_move"]["gcode_position"][2] = round(0.2 + progress * 23.8, 2)
                eo = self.status.get("exclude_object") or {}
                objs = eo.get("objects") or []
                excluded = set(eo.get("excluded_objects") or ())
                active_objs = [o["name"] for o in objs if o.get("name") not in excluded]
                eo["current_object"] = active_objs[0] if active_objs else None
                if progress >= 1.0:
                    stats["state"] = "complete"
                    self._finish_active_job("completed")
            ace = self.status.get("ace_instance_0")
            if ace:
                dry = ace["dryer_status"]
                drying = dry["status"] == "drying"
                goal = float(dry["target_temp"]) if drying else 24.0
                ace["temp"] = round(ace["temp"] + max(-2.0, min(4.0, goal - ace["temp"])) * dt)
                if drying:
                    remain = max(0, int(dry["remain_time"]) - int(round(60 * dt)))
                    if remain <= 0:
                        ace["dryer_status"] = {"status": "stop", "target_temp": 0, "duration": 0, "remain_time": 0}
                        ace["fan_speed"] = 0
                    else:
                        dry["remain_time"] = remain

    def respond(self, text):
        self.gcode_store.append({"message": text, "time": time.time(), "type": "response"})

    def run_gcode(self, script):
        with self.lock:
            self.gcodes.append(script)
            self.gcode_store.append({"message": script, "time": time.time(), "type": "command"})
            if self.fail_next_gcode:
                message, self.fail_next_gcode = self.fail_next_gcode, None
                self.gcode_store.append({"message": "!! " + str(message), "time": time.time(), "type": "response"})
                raise ValueError(message)
            for line in script.split("\n"):
                words = line.split()
                params = {w[0].upper(): w[1:] for w in words[1:] if "=" not in w}
                named = dict(w.upper().split("=", 1) for w in words[1:] if "=" in w)
                cmd = words[0].upper() if words else ""
                if cmd.startswith("ACE_") and self.fail_next_ace:
                    # the 0.2.0 driver reports a refused command with respond_raw("!! ACE[n]: ...") and HTTP 200
                    message, self.fail_next_ace = self.fail_next_ace, None
                    self.respond("!! ACE[0]: " + message)
                    continue
                if cmd in ("M104", "M109"):
                    self.status["extruder"]["target"] = float(params["S"])
                elif cmd in ("M140", "M190"):
                    self.status["heater_bed"]["target"] = float(params["S"])
                elif cmd == "M106":
                    self.status["fan"]["speed"] = float(params.get("S", 255)) / 255
                elif cmd == "M107":
                    self.status["fan"]["speed"] = 0.0
                elif cmd == "G28":
                    self.status["toolhead"]["homed_axes"] = "xyz"
                elif cmd == "M84":
                    self.status["toolhead"]["homed_axes"] = ""
                elif cmd == "G1":
                    for i, axis in enumerate("XYZ"):
                        if axis in params:
                            self.status["toolhead"]["position"][i] += float(params[axis])
                elif cmd == "TURN_OFF_HEATERS":
                    self.status["extruder"]["target"] = self.status["heater_bed"]["target"] = 0.0
                elif cmd == "M220":
                    self.status["gcode_move"]["speed_factor"] = float(params["S"]) / 100
                elif cmd == "M221":
                    self.status["gcode_move"]["extrude_factor"] = float(params["S"]) / 100
                elif cmd == "SET_GCODE_OFFSET" and "Z_ADJUST" in named:
                    origin = self.status["gcode_move"]["homing_origin"]
                    origin[2] = round(origin[2] + float(named["Z_ADJUST"]), 4)
                elif cmd in ("NOZZLE_LIGHT_TOGGLE", "LOGO_LIGHT_TOGGLE"):
                    pin = self.status["output_pin " + cmd[:-7].lower()]
                    pin["value"] = 0.0 if pin["value"] else 1.0
                elif cmd == "LEVIQ_AUTO_ZOFFSET_ON_OFF":
                    self.status["leviq3"]["auto_zoffset_on_off"] = int(named["ON_OFF"])
                elif cmd == "ACE_START_DRYING":
                    duration_min = int(named.get("DURATION", 240))
                    self.status["ace_instance_0"]["dryer_status"] = {
                        "status": "drying",
                        "target_temp": int(named["TEMP"]),
                        "duration": duration_min,
                        "remain_time": duration_min * 60,
                    }
                    self.status["ace_instance_0"]["fan_speed"] = 7000
                elif cmd == "ACE_STOP_DRYING":
                    self.status["ace_instance_0"]["dryer_status"] = {"status": "stop", "target_temp": 0,
                                                                     "duration": 0, "remain_time": 0}
                    self.status["ace_instance_0"]["fan_speed"] = 0
                elif cmd == "ACE_CHANGE_TOOL":
                    self.status["ace_state"]["current_index"] = int(named["TOOL"])
                elif cmd == "ACE_SET_SLOT":
                    slot = self.status["ace_instance_0"]["slots"][int(named["T"])]
                    if named.get("EMPTY") == "1":
                        slot.update(status="empty", material="", color=[0, 0, 0], temp=0, rfid=False)
                        for k in ("sku", "brand", "extruder_temp", "hotbed_temp", "diameter", "total", "current"):
                            slot.pop(k, None)
                    else:
                        slot.update(status="ready", material=named["MATERIAL"], temp=int(named["TEMP"]),
                                    color=[int(c) for c in named["COLOR"].split(",")], rfid=False)
                elif cmd == "ACE_ENABLE_RFID_SYNC":
                    self.status["ace_instance_0"]["rfid_sync_enabled"] = True
                elif cmd == "ACE_DISABLE_RFID_SYNC":
                    self.status["ace_instance_0"]["rfid_sync_enabled"] = False
                elif cmd == "ACE_ENABLE_ENDLESS_SPOOL":
                    self.status["ace_state"]["endless_spool_enabled"] = True
                elif cmd == "ACE_DISABLE_ENDLESS_SPOOL":
                    self.status["ace_state"]["endless_spool_enabled"] = False
                elif cmd == "ACE_SET_ENDLESS_SPOOL_MODE":
                    mode = named.get("MODE", "exact").lower()
                    if mode in ("exact", "material", "next"):
                        self.status["ace_state"]["endless_spool_match_mode"] = mode
                elif cmd == "ACE_ENABLE_FEED_ASSIST":
                    slot_idx = int(named["T"]) % 4 if "T" in named else int(named.get("INDEX", 0))
                    self.status["ace_instance_0"]["feed_assist_slot"] = slot_idx
                elif cmd == "ACE_DISABLE_FEED_ASSIST":
                    self.status["ace_instance_0"]["feed_assist_slot"] = -1
                elif cmd == "ACE_SET_FEED_SPEED":
                    if "feed_speed" in self.status["ace_instance_0"] and "SPEED" in named:
                        self.status["ace_instance_0"]["feed_speed"] = int(float(named["SPEED"]))
                elif cmd == "ACE_SET_RETRACT_SPEED":
                    if "retract_speed" in self.status["ace_instance_0"] and "SPEED" in named:
                        self.status["ace_instance_0"]["retract_speed"] = int(float(named["SPEED"]))
                elif cmd == "ACE_TANGLE_PROTECTION":
                    tp = self.status["ace_instance_0"].get("tangle_protection")
                    if isinstance(tp, dict):
                        if "ENABLE" in named:
                            tp["enabled"] = bool(int(float(named["ENABLE"])))
                        if "THRESHOLD" in named:
                            tp["threshold"] = float(named["THRESHOLD"])
                elif cmd == "ACE_FULL_UNLOAD":
                    self.status["ace_state"]["current_index"] = -1
                    if named.get("TOOL", "").upper() == "ALL":
                        self.status["ace_instance_0"]["feed_assist_slot"] = -1
                elif cmd == "ACE_RESET_ACTIVE_TOOLHEAD":
                    self.status["ace_state"]["current_index"] = -1
                    self.status["ace_instance_0"]["feed_assist_slot"] = -1
                elif cmd == "ACE_RESET_PERSISTENT_INVENTORY":
                    for slot in self.status["ace_instance_0"]["slots"]:
                        slot.update(status="empty", material="", color=[0, 0, 0], temp=0, rfid=False)
                        for k in ("sku", "brand", "extruder_temp", "hotbed_temp", "diameter", "total", "current"):
                            slot.pop(k, None)
                elif cmd == "EXCLUDE_OBJECT":
                    eo = self.status.setdefault("exclude_object", {"objects": [], "excluded_objects": [], "current_object": None})
                    reset = named.get("RESET")
                    current = named.get("CURRENT")
                    name = named.get("NAME", "").upper()
                    if reset is not None:
                        if name and name in eo["excluded_objects"]:
                            eo["excluded_objects"] = [x for x in eo["excluded_objects"] if x != name]
                        elif not name:
                            eo["excluded_objects"] = []
                    elif name:
                        if name not in eo["excluded_objects"]:
                            eo["excluded_objects"] = sorted(eo["excluded_objects"] + [name])
                        if eo.get("current_object") == name:
                            active_objs = [o["name"] for o in eo["objects"] if o.get("name") not in eo["excluded_objects"]]
                            eo["current_object"] = active_objs[0] if active_objs else None
                    elif current is not None and eo.get("current_object"):
                        cur_name = eo["current_object"]
                        if cur_name not in eo["excluded_objects"]:
                            eo["excluded_objects"] = sorted(eo["excluded_objects"] + [cur_name])
                        eo["current_object"] = None
                elif cmd in ("ACE_FEED", "ACE_STOP_FEED", "ACE_RETRACT", "ACE_STOP_RETRACT",
                             "ACE_SMART_LOAD", "ACE_SMART_UNLOAD", "ACE_SAVE_INVENTORY",
                             "ACE_FLUSH", "ACE_RECONNECT", "ACE_GET_STATUS"):
                    pass
                if cmd.startswith("ACE_") and self.ace_info_lines:
                    # respond_info() prefixes "// "; the driver answers every command it accepted
                    if cmd == "ACE_START_DRYING":
                        self.respond("// ACE[0]: Dryer started: %s°C for %s min" % (named.get("TEMP"), named.get("DURATION", 240)))
                    elif cmd == "ACE_STOP_DRYING":
                        self.respond("// ACE[0]: Dryer stopped")
                    else:
                        self.respond("// ACE[0]: %s done" % cmd)

    def handle(self, method, path, query, body):
        if path == "/printer/info":
            return {"state": self.klippy, "state_message": "Printer is " + self.klippy,
                    "software_version": "v0.13.0-412", "hostname": "kobra3"}
        if path == "/printer/objects/query":
            objects = body["objects"]
            if "configfile" in objects:
                return {"status": {"configfile": {"settings": self.settings}}}
            with self.lock:
                return {"status": json.loads(json.dumps({k: self.status[k] for k in objects if k in self.status}))}
        if path == "/printer/gcode/script":
            self.run_gcode(body["script"])
            return "ok"
        if path == "/server/files/list":
            return self.files
        if path == "/server/files/metadata":
            return dict(self.metadata, filename=query["filename"][0])
        if path == "/server/history/list":
            with self.lock:
                limit = int((query.get("limit") or [50])[0])
                start = int((query.get("start") or [0])[0])
                order = str((query.get("order") or ["desc"])[0]).lower()
                existing_paths = {f.get("path") for f in self.files}
                jobs = [dict(j, exists=(j["filename"] in existing_paths)) for j in self.history_jobs]
                if order == "asc":
                    jobs = list(reversed(jobs))
                sliced = jobs[start:start + limit] if limit > 0 else jobs[start:]
                return {"count": len(sliced), "jobs": json.loads(json.dumps(sliced))}
        if path == "/server/history/totals":
            with self.lock:
                return {"job_totals": dict(self.history_totals)}
        if path == "/server/temperature_store":
            with self.lock:
                if not self.temperature_store_enabled:
                    raise KeyError(path)
                return copy.deepcopy(self.temperature_store)
        if path == "/server/gcode_store":
            with self.lock:
                count = int((query.get("count") or [100])[0])
                items = self.gcode_store[-count:] if count > 0 else list(self.gcode_store)
                return {"gcode_store": copy.deepcopy(items)}
        if path == "/machine/update/status":
            with self.lock:
                if self.update_status is None:
                    raise KeyError(path)
                return copy.deepcopy(self.update_status)
        stats = self.status["print_stats"]
        if path == "/printer/print/start":
            fname = query["filename"][0]
            stats.update(state="printing", filename=fname, print_duration=0.0, filament_used=0.0)
            self.status["virtual_sdcard"]["progress"] = 0.0
            if fname == "kutu_01.gcode":
                self.status["exclude_object"] = {"objects": [], "excluded_objects": [], "current_object": None}
            else:
                self.status["exclude_object"] = {
                    "objects": [
                        {"name": "KUTU_GOVDE"},
                        {"name": "KUTU_KAPAK"},
                        {"name": "KLIPS_1"},
                        {"name": "KLIPS_2"},
                    ],
                    "excluded_objects": [],
                    "current_object": "KUTU_GOVDE",
                }
            self._active_job = {
                "job_id": "%06X" % (len(self.history_jobs) + 1),
                "exists": True,
                "filename": fname,
                "status": "in_progress",
                "start_time": 1763250000.0,
                "end_time": None,
                "print_duration": 0.0,
                "total_duration": 0.0,
                "filament_used": 0.0,
                "metadata": dict(self.metadata),
            }
        elif path == "/printer/print/pause":
            stats["state"] = "paused"
        elif path == "/printer/print/resume":
            stats["state"] = "printing"
        elif path == "/printer/print/cancel":
            stats["state"] = "cancelled"
            self._finish_active_job("cancelled")
        elif path == "/machine/reboot":
            self.rebooted = True
        elif path == "/printer/emergency_stop":
            self.klippy = "shutdown"
        elif path == "/printer/firmware_restart":
            try:
                self.reload_config()
                self.klippy = "ready"
            except ValueError as exc:
                self.klippy = "error"
                raise ValueError(str(exc)) from exc
        else:
            raise KeyError(path)
        return "ok"


def _parse_multipart(content_type, raw_body):
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[9:].strip('"').encode("utf-8")
            break
    if not boundary:
        raise ValueError("Missing multipart boundary")
    fields = {}
    files = {}
    for chunk in raw_body.split(b"--" + boundary):
        chunk = chunk.lstrip(b"\r\n")
        if not chunk or chunk.startswith(b"--"):
            continue
        sep = b"\r\n\r\n"
        pos = chunk.find(sep)
        if pos < 0:
            continue
        head = chunk[:pos].decode("utf-8", errors="replace")
        val = chunk[pos + len(sep):]
        if val.endswith(b"\r\n"):
            val = val[:-2]
        name = filename = None
        for line in head.split("\r\n"):
            if not line.lower().startswith("content-disposition:"):
                continue
            for attr in line.split(";"):
                attr = attr.strip()
                if attr.startswith("name="):
                    name = attr.split("=", 1)[1].strip('"\r\n')
                elif attr.startswith("filename="):
                    filename = attr.split("=", 1)[1].strip('"\r\n')
        if filename is not None and name is not None:
            files[name] = (filename, val)
        elif name is not None:
            fields[name] = val.decode("utf-8", errors="replace")
    return fields, files


def serve(printer, port=0, simulate=True):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, payload, code=200):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self):
            url = urllib.parse.urlsplit(self.path)
            if self.command == "GET" and url.path.startswith("/server/files/gcodes/"):
                data = printer.thumbnail()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if url.path.startswith("/server/files/config/"):
                rel = urllib.parse.unquote(url.path[len("/server/files/config/"):]).lstrip("/")
                if self.command == "GET":
                    if rel not in printer.config_files:
                        self._send_json({"error": {"code": 404, "message": "Not Found"}}, 404)
                        return
                    data = printer.config_files[rel]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if self.command == "DELETE":
                    if rel not in printer.config_files:
                        self._send_json({"error": {"code": 404, "message": "Not Found"}}, 404)
                        return
                    del printer.config_files[rel]
                    self._send_json({"result": {"action": "delete_file", "item": {"path": rel, "root": "config"}}})
                    return
            if self.command == "POST" and url.path == "/server/files/upload":
                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length) if length else b""
                try:
                    fields, files = _parse_multipart(self.headers.get("Content-Type", ""), raw_body)
                    root = fields.get("root", "gcodes")
                    if "file" not in files:
                        raise ValueError("Missing file field")
                    filename, data = files["file"]
                    subpath = fields.get("path", "").strip("/")
                    rel = (subpath + "/" + filename) if subpath else filename
                    if root == "config":
                        printer.config_files[rel] = data
                    self._send_json({"item": {"path": rel, "root": root, "size": len(data)},
                                     "action": "create_file"}, 201)
                except ValueError as exc:
                    self._send_json({"error": {"code": 400, "message": str(exc)}}, 400)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            try:
                payload, code = {"result": printer.handle(self.command, url.path, urllib.parse.parse_qs(url.query),
                                                          body)}, 200
            except KeyError:
                payload, code = {"error": {"code": 404, "message": "Not Found"}}, 404
            except ValueError as exc:
                payload, code = {"error": {"code": 400, "message": str(exc)}}, 400
            self._send_json(payload, code)

        do_GET = do_POST = do_DELETE = _handle

        def log_message(self, fmt, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if simulate:
        def loop():
            while True:
                time.sleep(1)
                printer.tick()
        threading.Thread(target=loop, daemon=True).start()
    return httpd


class FakeWifiManager:
    """In-memory Wi-Fi manager for previews and UI tests."""

    def __init__(self, initial_ssid="AtolyeWiFi", initial_ip="192.168.1.42"):
        from kobra_screen.network import WifiNetwork, WifiStatus

        self._WifiNetwork = WifiNetwork
        self._WifiStatus = WifiStatus
        self.ssid = initial_ssid
        self.ip_address = initial_ip
        self.signal_dbm = -52
        self.state = "COMPLETED" if initial_ssid else "DISCONNECTED"
        self.saved = {"AtolyeWiFi": "sifre123"} if initial_ssid else {}
        self.visible = [
            {"ssid": "AtolyeWiFi", "bssid": "aa:bb:cc:dd:ee:01", "freq": 2437, "signal": -52, "flags": "[WPA2-PSK-CCMP][ESS]"},
            {"ssid": "Ev_5G", "bssid": "aa:bb:cc:dd:ee:02", "freq": 5180, "signal": -61, "flags": "[WPA2-PSK-CCMP][ESS]"},
            {"ssid": "Misafir", "bssid": "aa:bb:cc:dd:ee:03", "freq": 2412, "signal": -74, "flags": "[ESS]"},
            {"ssid": "Komsu", "bssid": "aa:bb:cc:dd:ee:04", "freq": 2462, "signal": -82, "flags": "[WPA2-PSK-CCMP][ESS]"},
            {"ssid": "LabNet", "bssid": "aa:bb:cc:dd:ee:05", "freq": 2437, "signal": -88, "flags": "[WPA2-PSK-CCMP][ESS]"},
        ]
        self.calls = []

    def get_status(self):
        return self._WifiStatus(
            interface="wlan0",
            iface_up=True,
            wpa_running=True,
            udhcpc_running=True,
            state=self.state,
            ssid=self.ssid,
            ip_address=self.ip_address if self.state == "COMPLETED" else "",
            signal_dbm=self.signal_dbm if self.state == "COMPLETED" else None,
            saved_ssids=tuple(self.saved.keys()),
            wpa_conf_path="/userdata/wifi_cfg/wpa_supplicant.conf",
        )

    def scan(self):
        self.calls.append(("scan",))
        return self.scan_results()

    def scan_results(self):
        nets = []
        for item in self.visible:
            s = item["ssid"]
            is_cur = self.state == "COMPLETED" and self.ssid == s
            nets.append(self._WifiNetwork(
                ssid=s,
                bssid=item["bssid"],
                frequency=item["freq"],
                signal_dbm=item["signal"],
                flags=item["flags"],
                secured=("WPA" in item["flags"] or "WEP" in item["flags"]),
                saved=(s in self.saved),
                connected=is_cur,
            ))
        return nets

    def connect(self, ssid, password=None, flags=""):
        from kobra_screen.network import validate_password, validate_ssid

        validate_ssid(ssid)
        validate_password(password, flags=flags)
        self.calls.append(("connect", ssid))
        if password is not None:
            self.saved[ssid] = password
        elif ssid not in self.saved:
            self.saved[ssid] = None
        self.ssid = ssid
        self.state = "COMPLETED"
        self.ip_address = "192.168.1.42"
        match = next((n for n in self.visible if n["ssid"] == ssid), None)
        self.signal_dbm = match["signal"] if match else -58

    def forget(self, ssid):
        self.calls.append(("forget", ssid))
        self.saved.pop(ssid, None)
        if self.ssid == ssid:
            self.ssid = ""
            self.state = "DISCONNECTED"
            self.ip_address = ""

    def ensure_connected(self):
        return False


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7125
    serve(FakePrinter(), port)
    print("sahte Moonraker: http://127.0.0.1:%d" % port, flush=True)
    while True:
        time.sleep(3600)
