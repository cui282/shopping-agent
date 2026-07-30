# Ship a self-hosted beta before a hosted service

The Engineering Beta is distributed as public source code for local or controlled self-hosting, not
as a project-operated public multi-tenant service. Anonymous identifiers are sufficient for the beta
experience, while any future hosted service must first add authenticated identity, resource
ownership, rate limiting, and user-data deletion rather than treating client-supplied IDs as access
control.
