# Flight Monitor

This directory builds a static, read-only Vercel page from telemetry collected on the Windows host. The host must remain signed in, awake, connected to the network, and running `scripts/flight_supervisor.py`; Vercel does not monitor or restart the trading/rehearsal processes.

The page is public unless Vercel project access controls are configured. Do not publish process IDs, command lines, local paths, credentials, or account identifiers. Supervisor snapshots are allowlisted before they reach the deployed history.

The recommended local launch is the per-user Task Scheduler installer:

```powershell
.\scripts\install_flight_supervisor.ps1 -DurationHours 101 -StartNow
```

It starts the four-day shadow-only supervisor, sets a Windows system-awake request, retries a crashed task, and does not run elevated. It requires the user to remain signed in; it cannot protect against power loss, Windows restart/update, network outage, Vercel outage, or machine failure. Task Scheduler's restart recovery and the supervisor's internal bounded retries mitigate ordinary process exits but are not high availability.

The supervisor uses existing pinned shadow stores. It does not reset, delete, or modify `data/orders.db`; it may continue writing the configured shadow databases. It never starts the real-money Trader.

Before travel, verify the local status page and its deployed URL from a phone on cellular, and verify the computer is on AC power with automatic sleep disabled or the supervisor's awake request active. Stop the scheduled task after the monitoring window:

```powershell
Stop-ScheduledTask -TaskName 'SpreadHunter-FlightSupervisor'
```

Remote access should use an authenticated/private Vercel deployment or a private VPN to a read-only dashboard. Never expose the local dashboard directly to the public internet: it is an operations surface, not a public status API.
