"""LeadStack product package."""

import zoneinfo

# Time zone rules come from the lock-pinned `tzdata` package, never from the
# host operating system: slot computation and DST behaviour (including
# Morocco's Ramadan offset changes) must not differ between developer
# machines, CI and production images. See docs/adr/0008-appointments.md.
zoneinfo.reset_tzpath(to=[])
zoneinfo.ZoneInfo.clear_cache()

__version__ = "0.1.0"
