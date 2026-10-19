"""Mini control app (Phase 31; ADRs 0015/0016).

A small server-rendered web app on the home LAN for both parents: status,
upcoming events and important dates (read-only), Google re-authorization,
and a few guarded admin actions.

Contract: nothing else in the system imports or calls this package. If the app
is down, the listener and the scheduled jobs keep running unchanged (ground
rule 8). The app never writes Google Calendar, never runs a scheduled job and
has no background scheduler. Do not import Starlette or other web packages at
this package's top level: the ``control`` extra is optional, and the listener
environment must not load them (a test enforces it).
"""
