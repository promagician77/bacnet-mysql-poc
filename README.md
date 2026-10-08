# BACnet to MySQL - phase 1 notes

Static site, no build step. Four tabs: the coverage measurement, the architecture,
the database design, and working notes.

Reproduce the numbers:

    python simulate.py      # writes site/sim.json

`simulate.py` drives a synthetic 24-hour BACnet site and collects it two ways - COV
subscriptions alone, and COV plus a 10-minute reconciliation poll - then scores both
against the simulator's own values. The failure modes injected (unconfirmed
notifications dropped, subscriptions lapsing, a device going offline) are the ones
the YABE README documents in sections 2.6.2 and 2.6.3.

`files/schema.sql` is the MySQL DDL: readings keep their BACnet statusFlags and
their source, and the fact table clusters on (object_id, read_at) so a trend query
is a range scan.

Run locally: `npm start` -> http://localhost:3000
Deploy: `npm run deploy`, or import the folder in Vercel ("Other" preset, no build command).

The site is simulated. No customer equipment was touched.
