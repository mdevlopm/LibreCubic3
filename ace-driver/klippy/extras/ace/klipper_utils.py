"""Small helpers for talking to Klipper from driver code that is NOT running
inside a G-code command (reactor callbacks, timers)."""


def pause_print_from_event(printer, gcode):
    """Pause the print the way Klipper's own filament_switch_sensor does.

    ``gcode.run_script_from_command()`` does not take ``gcode.mutex`` (it is
    meant for use from inside a G-code handler). Called from a reactor callback
    it would run PAUSE in the middle of whatever command currently holds the
    mutex (a move sequence, ``M109``, a macro), corrupting the saved PAUSE
    state. From an event the correct sequence is:

    1. ``pause_resume.send_pause_command()`` - stops virtual_sdcard reading
       immediately (the current line finishes);
    2. ``gcode.run_script("PAUSE")`` - queued behind the running command via
       the G-code mutex.

    Must be called from its own greenlet (``reactor.register_callback``): the
    mutex wait pauses the calling greenlet.
    """
    pause_resume = printer.lookup_object("pause_resume", None)
    send = getattr(pause_resume, "send_pause_command", None)
    if callable(send):
        send()
    runner = getattr(gcode, "run_script", None) or gcode.run_script_from_command
    runner("PAUSE")
