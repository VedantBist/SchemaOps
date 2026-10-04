# ULPF demo UI

The console currently opens in ULPF-only mode. All eleven Log pipeline pages,
including privacy, compliance, detections, entities and benchmarks, remain available.
The original CausalOps pages and shell are retained, but are not mounted through
navigation or direct links in this mode. Old AIOps links redirect to Log sources.
The header and sidebar footer show only ULPF information.

To restore the complete interface, set `ULPF_DEMO_MODE` to `false` in
`src/config/uiMode.ts`, then rebuild the frontend:

```sh
docker compose up -d --no-deps --build frontend
```

This setting only changes the UI. It does not disable backend services, policies,
automation, data collection or stored history, and it is not an authorization control.
